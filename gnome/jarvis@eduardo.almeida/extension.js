// Jarvis na barra do GNOME: um ponto que mostra o que ele está fazendo, e um menu.
//
// O estado vem de $XDG_RUNTIME_DIR/jarvis/estado.json, que os scripts do Jarvis gravam
// (estado.py). Esta extensão só LÊ: não tem lógica do Jarvis aqui dentro, de propósito.
// Quanto menos ela faz, menos quebra quando o GNOME atualiza.
//
// Cores: cinza parado · azul ouvindo · âmbar pensando · verde falando.
// Só o contorno: a escuta do "Ei Jarvis" está desligada (o Insert continua valendo).

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const HOME = GLib.get_home_dir();
const BIN = `${HOME}/.claude/bin`;
const ESTADO = `${GLib.get_user_runtime_dir()}/jarvis/estado.json`;
const AGENDA = `${HOME}/.claude/cache/agenda.json`;
const LER_MS = 500;           // o ponto acompanha o arquivo de estado nesse ritmo
const ESCUTA_MS = 10000;      // conferir se o serviço do "Ei Jarvis" está ligado

const TEXTOS = {
    parado: 'Parado — diga “Ei Jarvis”',
    ouvindo: 'Ouvindo…',
    pensando: 'Pensando…',
    falando: 'Falando',
};

const decoder = new TextDecoder();

function rodar(argv, aoTerminar) {
    // tudo assíncrono: nada pode travar a barra do GNOME esperando um script
    try {
        const proc = Gio.Subprocess.new(argv,
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        proc.communicate_utf8_async(null, null, (p, res) => {
            let saida = '';
            try {
                [, saida] = p.communicate_utf8_finish(res);
            } catch (e) {
                saida = '';
            }
            aoTerminar?.(p.get_successful(), (saida ?? '').trim());
        });
    } catch (e) {
        aoTerminar?.(false, '');
    }
}

const Indicador = GObject.registerClass(
class Indicador extends PanelMenu.Button {
    _init() {
        super._init(0.5, 'Jarvis');
        this._ponto = new St.Widget({
            style_class: 'jarvis-ponto jarvis-parado',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this.add_child(this._ponto);
        this._estado = 'parado';
        this._escutaLigada = true;
        this._pulsando = false;

        this._itemEstado = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._itemEstado.label.add_style_class_name('jarvis-titulo');
        this.menu.addMenuItem(this._itemEstado);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._itemReuniao = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._itemTocando = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._itemLembretes = new PopupMenu.PopupMenuItem('', {reactive: false});
        for (const item of [this._itemReuniao, this._itemTocando, this._itemLembretes])
            this.menu.addMenuItem(item);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._chave = new PopupMenu.PopupSwitchMenuItem('Ouvir “Ei Jarvis”', true);
        this._chave.connect('toggled', () => {
            rodar([`${BIN}/escuta-alterna.sh`], () => this._conferirEscuta());
        });
        this.menu.addMenuItem(this._chave);
        const tela = new PopupMenu.PopupMenuItem('Abrir a tela do Jarvis');
        tela.connect('activate', () => rodar(['python3', `${BIN}/tela.py`, 'abrir']));
        this.menu.addMenuItem(tela);
        const atualizar = new PopupMenu.PopupMenuItem('Atualizar o GitLab agora');
        atualizar.connect('activate', () => rodar([`${BIN}/atualiza.sh`]));
        this.menu.addMenuItem(atualizar);

        this.menu.connect('open-state-changed', (_m, aberto) => {
            if (aberto)
                this._preencherMenu();
        });

        this._timerEstado = GLib.timeout_add(GLib.PRIORITY_DEFAULT, LER_MS, () => {
            this._lerEstado();
            return GLib.SOURCE_CONTINUE;
        });
        this._timerEscuta = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ESCUTA_MS, () => {
            this._conferirEscuta();
            return GLib.SOURCE_CONTINUE;
        });
        this._conferirEscuta();
        this._lerEstado();
    }

    _lerEstado() {
        const arquivo = Gio.File.new_for_path(ESTADO);
        arquivo.load_contents_async(null, (f, res) => {
            let estado = 'parado', detalhe = '';
            try {
                const [, bytes] = f.load_contents_finish(res);
                const dados = JSON.parse(decoder.decode(bytes));
                const velho = dados.validade > 0 && Date.now() / 1000 - dados.desde > dados.validade;
                if (!velho && TEXTOS[dados.estado]) {
                    estado = dados.estado;
                    detalhe = dados.detalhe ?? '';
                }
            } catch (e) {
                // sem arquivo ainda: parado
            }
            this._aplicar(estado, detalhe);
        });
    }

    _aplicar(estado, detalhe) {
        this._detalhe = detalhe;
        if (estado === this._estado && this._classeAplicada)
            return;
        this._estado = estado;
        this._classeAplicada = true;
        const classe = estado === 'parado' && !this._escutaLigada ? 'jarvis-desligado' : `jarvis-${estado}`;
        this._ponto.style_class = `jarvis-ponto ${classe}`;
        this._pulsar(estado === 'ouvindo' || estado === 'falando');
        this._atualizarTextoEstado();
    }

    _pulsar(sim) {
        if (sim === this._pulsando)
            return;
        this._pulsando = sim;
        this._ponto.remove_all_transitions();
        this._ponto.opacity = 255;
        if (sim) {
            this._ponto.ease({
                opacity: 110,
                duration: 650,
                mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
                repeatCount: -1,
                autoReverse: true,
            });
        }
    }

    _atualizarTextoEstado() {
        let texto = TEXTOS[this._estado];
        if (this._estado === 'parado' && !this._escutaLigada)
            texto = 'Escuta desligada — use o Insert';
        if (this._estado === 'falando' && this._detalhe)
            texto = `Falando: ${this._detalhe.slice(0, 60)}${this._detalhe.length > 60 ? '…' : ''}`;
        this._itemEstado.label.text = texto;
    }

    _conferirEscuta() {
        rodar(['systemctl', '--user', 'is-active', 'jarvis-escuta'], (_ok, saida) => {
            const ligada = saida === 'active';
            if (ligada !== this._escutaLigada) {
                this._escutaLigada = ligada;
                this._classeAplicada = false;
                this._aplicar(this._estado, this._detalhe);
            }
            this._chave.setToggleState(ligada);
        });
    }

    _preencherMenu() {
        this._atualizarTextoEstado();
        this._itemReuniao.label.text = this._proximaReuniao();
        this._itemTocando.label.text = '♪ …';
        rodar(['python3', `${BIN}/acao.py`, 'agora'], (_ok, saida) => {
            this._itemTocando.label.text = `♪ ${saida.replace(/^Está tocando /, '').replace(/^Está pausado: /, 'pausado: ') || 'nada tocando'}`;
        });
        this._itemLembretes.label.text = '⏰ …';
        rodar(['python3', `${BIN}/lembrar.py`, 'listar'], (_ok, saida) => {
            const linhas = saida.split('\n').filter(l => l && !l.startsWith('nenhum'));
            this._itemLembretes.label.text = linhas.length
                ? `⏰ ${linhas.slice(0, 3).map(l => l.replace(/^(Lembrete|Rotina( \(comando\))?): /, '')).join('\n    ')}`
                : '⏰ nenhum lembrete';
        });
    }

    _proximaReuniao() {
        try {
            const [, bytes] = GLib.file_get_contents(AGENDA);
            const agora = new Date();
            const hoje = GLib.DateTime.new_now_local().format('%Y-%m-%d');
            const proximas = (JSON.parse(decoder.decode(bytes)).eventos ?? [])
                .filter(e => !e.dia_inteiro && e.inicio.slice(0, 10) === hoje && new Date(e.inicio) > agora)
                .sort((a, b) => a.inicio.localeCompare(b.inicio));
            if (!proximas.length)
                return '📅 nenhuma outra reunião hoje';
            const e = proximas[0];
            return `📅 ${e.inicio.slice(11, 16)} ${e.titulo}`;
        } catch (e) {
            return '📅 agenda indisponível';
        }
    }

    destroy() {
        for (const id of [this._timerEstado, this._timerEscuta]) {
            if (id)
                GLib.source_remove(id);
        }
        this._timerEstado = this._timerEscuta = 0;
        this._ponto?.remove_all_transitions();
        super.destroy();
    }
});

export default class JarvisExtension extends Extension {
    enable() {
        this._indicador = new Indicador();
        Main.panel.addToStatusArea(this.uuid, this._indicador, 0, 'right');
    }

    disable() {
        this._indicador?.destroy();
        this._indicador = null;
    }
}
