#!/usr/bin/env python3
"""Avisa 15 minutos antes de cada reunião, com botão para entrar na chamada.

Roda a cada minuto pelo timer sydle-lembrete (dias úteis, 7h às 20h). Só lê o
~/.claude/cache/agenda.json que o timer das 7h baixou — nunca chama a rede.

Janela: de 15 min antes até 5 min depois do início. O "depois" existe para o
computador que acordou da suspensão em cima da hora: melhor avisar "começou
há 2 min" do que ficar calado.

Cada reunião avisa uma vez. A chave é id + horário, então remarcar avisa de novo.

    lembrete.py              verifica e avisa (o timer chama assim)
    lembrete.py --teste      manda uma notificação de exemplo agora
"""
import datetime as dt
import json
import os
import subprocess
import sys

AGENDA = os.path.expanduser(os.environ.get("AGENDA", "~/.claude/cache/agenda.json"))
CARIMBOS = os.path.expanduser(os.environ.get("CARIMBOS", "~/.claude/cache/lembretes"))
BOTAO = os.path.expanduser("~/.claude/bin/botao.py")
FALAR = os.path.expanduser("~/.claude/bin/falar.py")
ANTES = dt.timedelta(minutes=15)
DEPOIS = dt.timedelta(minutes=5)


def agora():
    # AGORA=2026-09-28T13:32:00-03:00 permite testar sem esperar a reunião
    fixo = os.environ.get("AGORA")
    return dt.datetime.fromisoformat(fixo) if fixo else dt.datetime.now().astimezone()


def quando(delta):
    m = round(delta.total_seconds() / 60)
    if m > 1:
        return f"começa em {m} min"
    if m >= 0:
        return "começa agora"
    return f"começou há {-m} min"


def avisar(titulo, corpo, link):
    """Solta a notificação sem esperar: o botão fica vivo depois que este script sai.

    Quem mostra é o botao.py, que fala direto com o GNOME pelo D-Bus. O
    notify-send desta máquina recusa botão ("Actions are not supported"), mesmo
    com o gnome-shell anunciando que suporta.

    Roda em sessão própria; o serviço usa KillMode=process para o systemd não
    derrubá-lo. Ele termina sozinho quando você clica ou fecha a notificação.
    """
    subprocess.Popen([BOTAO, titulo, corpo, link or ""], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def falar(titulo, minutos):
    """A voz junto com a notificação. Solta, como o botão: o serviço usa KillMode=process.

    O falar.py fica calado sozinho com o Não perturbe e enfileira falas simultâneas.
    """
    subprocess.Popen([FALAR, "--reuniao", titulo, str(minutos)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def carimbados(dia):
    try:
        with open(os.path.join(CARIMBOS, f"{dia}.json"), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def carimbar(dia, chaves):
    os.makedirs(CARIMBOS, exist_ok=True)
    caminho = os.path.join(CARIMBOS, f"{dia}.json")
    with open(caminho + ".tmp", "w", encoding="utf-8") as f:
        json.dump(sorted(chaves), f)
    os.replace(caminho + ".tmp", caminho)
    for nome in os.listdir(CARIMBOS):  # carimbo de outro dia não serve mais
        if nome.endswith(".json") and nome != f"{dia}.json":
            os.remove(os.path.join(CARIMBOS, nome))


def pendentes(eventos, t, ja):
    """As reuniões dentro da janela que ainda não avisaram."""
    for ev in eventos:
        if ev.get("dia_inteiro"):
            continue
        ini = dt.datetime.fromisoformat(ev["inicio"])
        chave = f"{ev['id']}@{ev['inicio']}"
        if chave in ja:
            continue
        if -DEPOIS <= ini - t <= ANTES:
            yield chave, ev, ini


def main():
    if sys.argv[1:] == ["--teste"]:
        avisar("Daily - exemplo", "começa em 15 min · 13:45-14:00", "https://meet.google.com/")
        return 0

    try:
        with open(AGENDA, encoding="utf-8") as f:
            eventos = json.load(f).get("eventos") or []
    except (OSError, ValueError):
        return 0

    t = agora()
    dia = t.date().isoformat()
    ja = carimbados(dia)
    novos = set()
    for chave, ev, ini in pendentes(eventos, t, ja):
        fim = dt.datetime.fromisoformat(ev["fim"])
        avisar(ev["titulo"], f"{quando(ini - t)} · {ini:%H:%M}-{fim:%H:%M}", ev.get("link"))
        falar(ev["titulo"], round((ini - t).total_seconds() / 60))
        novos.add(chave)
    if novos:
        carimbar(dia, ja | novos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
