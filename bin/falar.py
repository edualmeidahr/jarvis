#!/usr/bin/env python3
"""A voz do Jarvis: fala um aviso curto com o Piper, offline.

    falar.py "texto"                        fala o texto
    falar.py --reuniao TITULO MINUTOS       "Daily em quinze minutos." (negativo = já começou)
    falar.py --bom-dia                      resumo do GitLab e da agenda de hoje
    falar.py --teste-dicionario             fala cada entrada do dicionário, para conferir
    falar.py --mostrar ...                  qualquer um acima, só imprimindo o que seria falado

Regras:
- não fala com o "Não perturbe" do GNOME ligado (a notificação na tela continua)
- uma fala de cada vez: dois avisos no mesmo minuto entram em fila, não se sobrepõem
- o dicionário ~/.config/jarvis/pronuncia.txt troca a palavra pela forma como ela
  deve SOAR ("daily = dêilí"); a notificação escrita não é afetada
- horário e contagem saem por extenso: o Piper lê "13:45" de um jeito estranho
"""
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile

# o WirePlumber memoriza o volume de cada tipo de stream e aplica no próximo. Um som do Jarvis
# que terminou abaixado (ducking) deixava todos os seguintes mudos — aconteceu duas vezes.
# Com esta propriedade, os sons dele sempre nascem em 100%
SEM_VOLUME_SALVO = ["-P", "{ state.restore-props = false }"]

PIPER = os.path.expanduser("~/.local/share/piper/piper/piper")
VOZ = os.path.expanduser("~/.local/share/piper/vozes/pt_BR-faber-medium.onnx")
# Ajustados de ouvido em 07/10 (a "Seis C, mais rápida"): mais firme, grave e encorpada.
# A versão de 29/09 era 0.80 / 0.12 / 0.9 / 0.7, sem o timbre.py
VELOCIDADE = 0.80  # length_scale: menor é mais rápido; 1.0 é o padrão do Piper
PAUSA = "0.15"     # sentence_silence: segundos entre frases
RITMO = "0.7"      # noise_w: variação da duração de cada som; mais baixo é mais firme (padrão 0.8)
TIMBRE = "0.55"    # noise_scale: variação do timbre; mais baixo é mais uniforme (padrão 0.667)
# corpo, compressão, sala e tom 8% mais grave. Sem ele (ou se falhar), a fala sai crua
TIMBRE_PY = os.path.expanduser("~/.claude/bin/timbre.py")
VENV_PY = os.path.expanduser("~/.local/share/jarvis/venv/bin/python")  # o timbre.py precisa do numpy
TOM = 0.92  # o mesmo do timbre.py: o Piper fala TOM vezes mais curto, e o tom mais grave devolve a duração
DICIONARIO = os.path.expanduser("~/.config/jarvis/pronuncia.txt")
GITLAB = os.path.expanduser("~/.claude/cache/gitlab.json")
AGENDA = os.path.expanduser("~/.claude/cache/agenda.json")
import importlib.util as _iu
_spec_e = _iu.spec_from_file_location("estado", os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado.py"))
estado = _iu.module_from_spec(_spec_e)
_spec_e.loader.exec_module(estado)  # o que o Jarvis está fazendo, para o ponto na barra do GNOME

FALANDO = os.path.join(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()), "jarvis", "falando.pid")
TRAVA = os.path.join(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()), "jarvis-voz.lock")

# pedaço de título de reunião que só diz de qual time é: dito em voz alta, é ruído
DO_TIME = re.compile(r"^(sybox\s+)?educ(ação)?$", re.I)


# ---------------------------------------------------------------- números

UNIDADES = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez",
            "onze", "doze", "treze", "catorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
DEZENAS = ["", "", "vinte", "trinta", "quarenta", "cinquenta"]


def extenso(n, feminino=False):
    """0 a 59, que é o que aviso de reunião e contagem precisam."""
    if n < 20:
        palavra = UNIDADES[n]
    else:
        d, u = divmod(n, 10)
        palavra = DEZENAS[d] + (f" e {UNIDADES[u]}" if u else "")
    if feminino:
        palavra = re.sub(r"\bum$", "uma", re.sub(r"\bdois$", "duas", palavra))
    return palavra


def hora_falada(h, m):
    base = "meio-dia" if h == 12 else extenso(h, feminino=True)
    if m == 0:
        return base if h == 12 else f"{base} {'hora' if h == 1 else 'horas'}"
    return f"{base} e {extenso(m)}"


def contagem(n, singular, plural, feminino=False):
    return f"{extenso(n, feminino) if n < 60 else n} {singular if n == 1 else plural}"


# ---------------------------------------------------------------- texto

def carregar_dicionario():
    pares = []
    try:
        for linha in open(DICIONARIO, encoding="utf-8"):
            linha = linha.split("#", 1)[0].strip()
            if "=" in linha:
                de, para = (x.strip() for x in linha.split("=", 1))
                if de and para:
                    pares.append((de, para))
    except OSError:
        pass
    # a expressão mais longa primeiro: "code reviewer" antes de "review"
    return sorted(pares, key=lambda p: -len(p[0]))


def pronunciar(texto):
    # código de issue lido em voz alta é letra e dígito soltos: some do áudio
    texto = re.sub(r"\s*\((?:[^()]*?\b[A-Z]{2,4}\d{5,6}\b[^()]*?)\)", "", texto)
    # fora de parênteses o código fica: tirar do meio da frase quebra a frase
    texto = re.sub(r"(?:\bMR\s+)?(?<![\w!])!(\d+)", r"MR \1", texto)  # "!197" e "MR !197" → "MR 197"
    for de, para in carregar_dicionario():
        texto = re.sub(rf"(?<![\w-]){re.escape(de)}(?![\w-])", para, texto, flags=re.I)
    return re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: hora_falada(int(m.group(1)), int(m.group(2))), texto)


def titulo_falado(titulo):
    """'SYBOX Educ - Produto - "Growth"/"Sharing"' → 'Produto, Growth e Sharing'."""
    partes = [p.strip().strip('"') for p in titulo.split(" - ")]
    partes = [p for p in partes if p and not DO_TIME.match(p)] or [titulo]
    return ", ".join(p.replace('"', "").replace("/", " e ") for p in partes)


def frase_reuniao(titulo, minutos):
    nome = titulo_falado(titulo)
    if minutos > 1:
        return f"{nome} em {extenso(minutos)} minutos."
    if minutos == 1:
        return f"{nome} em um minuto."
    if minutos == 0:
        return f"{nome} começando agora."
    return f"{nome} começou há {contagem(-minutos, 'minuto', 'minutos')}."


def frase_bom_dia(hoje=None):
    hoje = hoje or dt.date.today()
    partes = ["Bom dia."]
    try:
        g = json.load(open(GITLAB, encoding="utf-8"))
    except (OSError, ValueError):
        g = {}
    if g.get("gerado_em"):
        revs = len(g.get("reviews") or [])
        abertas = sum(m["threads_abertas"] for m in g.get("meus_mrs") or [])
        if revs:
            partes.append(f"Você tem {contagem(revs, 'review esperando', 'reviews esperando')}.")
        if abertas:
            partes.append(f"Seus MRs têm {contagem(abertas, 'thread aberta', 'threads abertas', feminino=True)}.")
        if not revs and not abertas:
            partes.append("Nada pendente no GitLab.")
    try:
        a = json.load(open(AGENDA, encoding="utf-8"))
    except (OSError, ValueError):
        a = {}
    if a.get("gerado_em"):
        iso = hoje.isoformat()
        do_dia = sorted((e for e in a.get("eventos") or [] if not e["dia_inteiro"] and e["inicio"][:10] == iso),
                        key=lambda e: e["inicio"])
        if do_dia:
            p = do_dia[0]
            ini = dt.datetime.fromisoformat(p["inicio"])
            quantas = contagem(len(do_dia), "reunião hoje", "reuniões hoje", feminino=True)
            primeira = "Ela é" if len(do_dia) == 1 else "A primeira é"
            partes.append(f"{quantas.capitalize()}. {primeira} {titulo_falado(p['titulo'])}, às {ini:%H:%M}.")
        else:
            partes.append("Nenhuma reunião hoje.")
    return " ".join(partes)


# ---------------------------------------------------------------- som

def nao_perturbe():
    r = subprocess.run(["gsettings", "get", "org.gnome.desktop.notifications", "show-banners"],
                       capture_output=True, text=True)
    return r.stdout.strip() == "false"


def tocar(texto):
    if nao_perturbe():
        return 0
    if not (os.path.exists(PIPER) and os.path.exists(VOZ)):
        print("falar: Piper ou a voz não estão em ~/.local/share/piper", file=sys.stderr)
        return 1
    with open(TRAVA, "w") as trava:
        fcntl.flock(trava, fcntl.LOCK_EX)  # fila: a segunda fala espera a primeira acabar
        with tempfile.NamedTemporaryFile(suffix=".wav", dir=os.path.dirname(TRAVA)) as wav, \
             tempfile.NamedTemporaryFile(suffix=".wav", dir=os.path.dirname(TRAVA)) as final:
            com_timbre = os.path.exists(TIMBRE_PY) and os.access(VENV_PY, os.X_OK)
            escala = VELOCIDADE * TOM if com_timbre else VELOCIDADE
            subprocess.run([PIPER, "--model", VOZ, "--length_scale", f"{escala:.3f}",
                            "--sentence_silence", PAUSA, "--noise_w", RITMO, "--noise_scale", TIMBRE,
                            "--output_file", wav.name],
                           input=pronunciar(texto), text=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            tocar_este = wav.name
            if com_timbre:
                r = subprocess.run([VENV_PY, TIMBRE_PY, wav.name, final.name], stderr=subprocess.DEVNULL, check=False)
                if r.returncode == 0:
                    tocar_este = final.name
                else:  # sem o timbre, a duração sairia curta: refaz cru, no ritmo certo
                    subprocess.run([PIPER, "--model", VOZ, "--length_scale", f"{VELOCIDADE:.3f}",
                                    "--sentence_silence", PAUSA, "--noise_w", RITMO, "--noise_scale", TIMBRE,
                                    "--output_file", wav.name], input=pronunciar(texto), text=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            # o pid fica num arquivo enquanto fala: "Ei Jarvis" no meio corta a fala (escuta.py)
            estado.marcar("falando", texto)
            tocador = subprocess.Popen(["pw-play", *SEM_VOLUME_SALVO, tocar_este], stderr=subprocess.DEVNULL)
            os.makedirs(os.path.dirname(FALANDO), exist_ok=True)
            with open(FALANDO, "w") as f:
                f.write(str(tocador.pid))
            try:
                tocador.wait()
            finally:
                estado.marcar("parado")
                try:
                    os.remove(FALANDO)
                except OSError:
                    pass
    return 0


def main():
    args = sys.argv[1:]
    mostrar = "--mostrar" in args
    args = [a for a in args if a != "--mostrar"]
    if not args:
        sys.exit(__doc__)

    if args[0] == "--teste-dicionario":
        falas = [f"{de}." for de, _ in reversed(carregar_dicionario())]
    elif args[0] == "--reuniao" and len(args) >= 3:
        falas = [frase_reuniao(args[1], int(args[2]))]
    elif args[0] == "--bom-dia":
        falas = [frase_bom_dia()]
    else:
        falas = [" ".join(args)]

    for f in falas:
        if mostrar:
            print(f"escrito: {f}\nfalado:  {pronunciar(f)}")
        else:
            tocar(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
