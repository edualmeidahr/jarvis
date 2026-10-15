#!/usr/bin/env python3
"""Modos de trabalho: reunião e foco. Cada um liga várias coisas de uma vez e desfaz tudo na saída.

    modo.py reuniao        liga o modo reunião; se já está ligado, desliga
    modo.py foco [min]     foco por N minutos (padrão 25); se já está ligado, desliga
    modo.py sair           desliga o modo que estiver ligado
    modo.py estado         o modo de agora, em JSON (o deck pergunta)
    modo.py fim-foco       o timer chama quando o foco acaba

Reunião: "não perturbe" ligado, microfone ligado, música pausada e o Meet da reunião aberto (a
mesma escolha do "entrar na reunião" falado). Na saída: notificações de volta e a música volta a
tocar, se tocava antes.

Foco: "não perturbe" ligado por N minutos, com um timer do systemd (do usuário, sem sudo). No fim,
"não perturbe" desligado e o Jarvis avisa falando. Com "não perturbe" o Jarvis não fala e o lembrete
de reunião não aparece: por isso o foco acaba 2 minutos antes da próxima reunião, e nem começa se
ela estiver a menos de 5 minutos.

O "não perturbe" é o do GNOME (show-banners), o mesmo que o falar.py e a bolha.py respeitam.
O estado fica em ~/.cache/jarvis/modo.json, fora do /run: o show-banners sobrevive a um
reinício, então o estado também precisa sobreviver, senão o "não perturbe" ficava ligado sem dono.
"""
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import time

BIN = os.path.dirname(os.path.realpath(__file__))
ESTADO = os.path.expanduser("~/.cache/jarvis/modo.json")
UNIDADE = "jarvis-foco"
FOCO_PADRAO = 25
FOLGA_REUNIAO = 2 * 60
FOCO_MINIMO = 5 * 60
NOTIFICACOES = ["org.gnome.desktop.notifications", "show-banners"]


def _modulo(nome):
    spec = importlib.util.spec_from_file_location(nome, os.path.join(BIN, f"{nome}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ler():
    try:
        return json.load(open(ESTADO))
    except (OSError, ValueError):
        return None


def gravar(e):
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    json.dump(e, open(ESTADO, "w"))


def nao_perturbe(ligar):
    """Liga ou desliga e devolve como estava antes ("true" = notificações aparecendo)."""
    antes = subprocess.run(["gsettings", "get", *NOTIFICACOES], capture_output=True, text=True).stdout.strip()
    subprocess.run(["gsettings", "set", *NOTIFICACOES, "false" if ligar else "true"], check=False)
    return antes or "true"


def proxima_reuniao():
    """Início da próxima reunião de hoje que ainda não começou, ou None."""
    try:
        evs = json.load(open(os.path.expanduser("~/.claude/cache/agenda.json"))).get("eventos") or []
    except (OSError, ValueError):
        return None
    agora = dt.datetime.now().astimezone()
    inicios = [dt.datetime.fromisoformat(e["inicio"]) for e in evs
               if not e.get("dia_inteiro") and e["inicio"][:10] == agora.date().isoformat()]
    futuras = [i for i in inicios if i > agora]
    return min(futuras) if futuras else None


def sair(e=None):
    """Desfaz o modo ligado. Devolve a frase, ou None se não havia modo."""
    e = e or ler()
    if not e:
        return None
    subprocess.run(["systemctl", "--user", "stop", f"{UNIDADE}.timer"], capture_output=True)
    subprocess.run(["gsettings", "set", *NOTIFICACOES, e.get("notificacoes_antes", "true")], check=False)
    try:
        os.remove(ESTADO)
    except OSError:
        pass
    if e["modo"] == "reuniao":
        if e.get("musica_tocava"):
            _modulo("acao").midia("continuar")
            return "Modo reunião desligado. A música voltou."
        return "Modo reunião desligado."
    return "Foco encerrado."


def reuniao():
    e = ler()
    if e and e["modo"] == "reuniao":
        return sair(e)
    sair(e)
    acao = _modulo("acao")
    t = acao.com_status_real(acao.tocando())
    tocava = bool(t and t["status"] == "Playing")
    antes = nao_perturbe(True)
    gravar({"modo": "reuniao", "desde": time.time(), "ate": None, "notificacoes_antes": antes,
            "musica_tocava": tocava})
    subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "0"], check=False)
    if tocava:  # pausa aqui: sem reunião na agenda, o entrar_na_reuniao volta antes de pausar
        acao.midia("pausar")
    falado, _ = _modulo("jarvis").entrar_na_reuniao("entrar na reunião", dt.datetime.now().astimezone())
    return f"Modo reunião ligado. {falado}"


def foco(minutos):
    e = ler()
    if e and e["modo"] == "foco":
        return sair(e)
    agora = time.time()
    ate = agora + minutos * 60
    aviso = ""
    r = proxima_reuniao()
    if r and r.timestamp() - FOLGA_REUNIAO < ate:
        ate = r.timestamp() - FOLGA_REUNIAO
        if ate - agora < FOCO_MINIMO:
            return f"Tem reunião às {r:%H:%M}. O foco fica para depois dela."
        aviso = f", por causa da reunião das {r:%H:%M}"
    sair(e)
    antes = nao_perturbe(True)
    gravar({"modo": "foco", "desde": agora, "ate": ate, "notificacoes_antes": antes})
    subprocess.run(["systemctl", "--user", "reset-failed", f"{UNIDADE}.timer", f"{UNIDADE}.service"],
                   capture_output=True)
    subprocess.run(["systemd-run", "--user", "--quiet", f"--unit={UNIDADE}", f"--on-active={round(ate - agora)}s",
                    "--timer-property=AccuracySec=1s", "--description=Jarvis: fim do foco",
                    "python3", os.path.join(BIN, "modo.py"), "fim-foco"], check=False)
    return f"Foco até {dt.datetime.fromtimestamp(ate):%H:%M}{aviso}. Notificações pausadas."


def fim_foco():
    e = ler()
    if not (e and e["modo"] == "foco"):
        return
    sair(e)  # primeiro o "não perturbe" sai: com ele ligado, o Jarvis não fala
    subprocess.run(["python3", os.path.join(BIN, "bolha.py"), "--nova", "--fim", "--importante", "--som", "ouvindo",
                    "Fim do foco", "Hora de uma pausa."], check=False)
    subprocess.run(["python3", os.path.join(BIN, "falar.py"), "Fim do foco. Hora de uma pausa."], check=False)


def estado():
    """O modo de agora. Foco vencido (o timer não sobrevive a reinício) é desfeito aqui."""
    e = ler()
    if e and e["modo"] == "foco" and e["ate"] and time.time() > e["ate"] + 60:
        sair(e)
        e = None
    return {"modo": e["modo"], "ate": e["ate"]} if e else {"modo": None, "ate": None}


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["reuniao"]:
        print(reuniao())
    elif args[:1] == ["foco"]:
        print(foco(int(args[1]) if len(args) > 1 and args[1].isdigit() else FOCO_PADRAO))
    elif args == ["sair"]:
        print(sair() or "Nenhum modo ligado.")
    elif args == ["estado"]:
        print(json.dumps(estado()))
    elif args == ["fim-foco"]:
        fim_foco()
    else:
        sys.exit(__doc__)
