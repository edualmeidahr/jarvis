#!/usr/bin/env python3
"""Lembretes por tempo e rotinas, com timers do systemd (do usuário, sem sudo).

    lembrar.py em 20m "tirar o café"            daqui a 20 minutos (s, m, h; "1h30m" também vale)
    lembrar.py as 15:30 "ligar para o suporte"   hoje às 15:30 (amanhã, se já passou)
    lembrar.py listar
    lembrar.py cancelar "café"                   cancela lembretes e rotinas que contêm o trecho

    lembrar.py rotina "Fri 17:00" "lançar as horas"           lembrete que se repete
    lembrar.py rotina "Mon..Fri 09:00" --comando "toca lofi"  frase falada ao Jarvis, que se repete

O calendário é o do systemd (OnCalendar): "Fri 17:00", "Mon..Fri 09:00", "*-*-01 10:00"
(todo dia 1º). Ele valida antes de criar. Lembrete solto some depois de tocar e não volta
se o computador desligar antes; rotina fica gravada em ~/.config/systemd/user e sobrevive.

Rotina com --comando só aceita comando local do Jarvis (tocar, abrir, volume, atualizar
o GitLab...): nada que chame o Claude roda sozinho, sem você ver.
"""
import datetime as dt
import os
import re
import subprocess
import sys
import time
import unicodedata

BIN = os.path.expanduser("~/.claude/bin")
UNIDADES = os.path.expanduser("~/.config/systemd/user")
PREFIXO_LEMBRETE, PREFIXO_ROTINA = "jarvis-lembrete-", "jarvis-rotina-"


def normalizar(t):
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()


def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def avisar_cmd(texto):
    """O que o timer roda: a bolha (fica no histórico) e a voz."""
    return [os.path.join(BIN, "aviso-lembrete.sh"), texto]


def duracao(texto):
    """'20m', '1h30m', '90s' → segundos."""
    partes = re.findall(r"(\d+)\s*([hms])", texto.lower())
    if not partes or re.sub(r"[\d\shms]", "", texto.lower()):
        return None
    return sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in partes)


def falar_hora(quando):
    hoje = dt.date.today()
    dia = "" if quando.date() == hoje else ("amanhã " if quando.date() == hoje + dt.timedelta(days=1) else f"{quando:%d/%m} ")
    return f"{dia}às {quando:%H:%M}"


def em(tempo, texto):
    s = duracao(tempo)
    if not s:
        return None, f"não entendi o tempo: {tempo}"
    unidade = f"{PREFIXO_LEMBRETE}{time.time_ns()}"
    r = subprocess.run(["systemd-run", "--user", "--quiet", f"--unit={unidade}", f"--on-active={s}s",
                        "--timer-property=AccuracySec=1s", f"--description=Lembrete: {texto}",
                        *avisar_cmd(texto)], capture_output=True, text=True)
    if r.returncode:
        return None, r.stderr.strip()
    quando = dt.datetime.now() + dt.timedelta(seconds=s)
    return unidade, f"lembrete {falar_hora(quando)}: {texto}"


def as_(hora, texto):
    m = re.fullmatch(r"(\d{1,2})(?::|h)?(\d{2})?", hora.strip().lower())
    if not m or int(m[1]) > 23 or int(m[2] or 0) > 59:
        return None, f"não entendi a hora: {hora}"
    agora = dt.datetime.now()
    quando = agora.replace(hour=int(m[1]), minute=int(m[2] or 0), second=0, microsecond=0)
    if quando <= agora:
        quando += dt.timedelta(days=1)
    unidade = f"{PREFIXO_LEMBRETE}{time.time_ns()}"
    r = subprocess.run(["systemd-run", "--user", "--quiet", f"--unit={unidade}",
                        f"--on-calendar={quando:%Y-%m-%d %H:%M:%S}", "--timer-property=AccuracySec=1s",
                        f"--description=Lembrete: {texto}", *avisar_cmd(texto)], capture_output=True, text=True)
    if r.returncode:
        return None, r.stderr.strip()
    return unidade, f"lembrete {falar_hora(quando)}: {texto}"


def slug(texto):
    s = re.sub(r"[^a-z0-9]+", "-", normalizar(texto)).strip("-")[:40]
    return s or "rotina"


def rotina(calendario, texto, comando=False):
    r = subprocess.run(["systemd-analyze", "calendar", calendario], capture_output=True, text=True)
    if r.returncode:
        return None, f"calendário inválido: {calendario}"
    proxima = (re.search(r"Next elapse:\s*(.+)", r.stdout) or [None, "?"])[1].strip()
    if comando:
        tipo = subprocess.run(["python3", os.path.join(BIN, "jarvis.py"), "--tipo", f"Jarvis, {texto}"],
                              capture_output=True, text=True).stdout.strip()
        if tipo not in ("comando", "acao", "tarefa"):
            return None, "rotina com comando só aceita comando local (tocar, abrir, volume, atualizar o GitLab)"
        exec_ = f'/usr/bin/python3 {os.path.join(BIN, "jarvis.py")} "Jarvis, {texto}"'
        descricao = f"Rotina (comando): {texto} — {calendario}"
    else:
        exec_ = " ".join(f'"{a}"' for a in avisar_cmd(texto))
        descricao = f"Rotina: {texto} — {calendario}"
    nome = f"{PREFIXO_ROTINA}{slug(texto)}"
    os.makedirs(UNIDADES, exist_ok=True)
    open(os.path.join(UNIDADES, f"{nome}.service"), "w").write(
        f"[Unit]\nDescription={descricao}\n\n[Service]\nType=oneshot\n"
        f"Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin\nExecStart={exec_}\n")
    open(os.path.join(UNIDADES, f"{nome}.timer"), "w").write(
        f"[Unit]\nDescription={descricao}\n\n[Timer]\nOnCalendar={calendario}\nAccuracySec=1s\n"
        f"Persistent=false\n\n[Install]\nWantedBy=timers.target\n")
    systemctl("daemon-reload")
    r = systemctl("enable", "--now", f"{nome}.timer")
    if r.returncode:
        return None, r.stderr.strip()
    return nome, f"rotina criada: {texto} ({calendario}); a próxima é {proxima}"


def listar():
    """[(unidade do timer, descrição, próxima vez)]."""
    r = systemctl("list-timers", "--all", "--no-legend", "--output=json")
    import json
    try:
        timers = json.loads(r.stdout or "[]")
    except ValueError:
        timers = []
    saida = []
    for t in timers:
        unidade = t.get("unit", "")
        if not unidade.startswith(("jarvis-lembrete-", "jarvis-rotina-")):
            continue
        desc = systemctl("show", "-p", "Description", "--value", unidade).stdout.strip()
        proxima = t.get("next")
        quando = dt.datetime.fromtimestamp(proxima / 1e6) if proxima else None
        saida.append((unidade, desc, quando))
    return sorted(saida, key=lambda x: x[2] or dt.datetime.max)


def cancelar(trecho):
    alvo = normalizar(trecho)
    feitos = []
    for unidade, desc, _ in listar():
        if alvo and alvo in normalizar(desc):
            base = unidade.removesuffix(".timer")
            systemctl("disable", "--now", unidade)
            systemctl("stop", f"{base}.service")
            for ext in (".timer", ".service"):
                try:
                    os.remove(os.path.join(UNIDADES, base + ext))  # só as rotinas têm arquivo
                except OSError:
                    pass
            feitos.append(desc)
    systemctl("daemon-reload")
    return feitos


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    acao = args[0]
    if acao == "em" and len(args) >= 3:
        _, frase = em(args[1], " ".join(args[2:]))
    elif acao == "as" and len(args) >= 3:
        _, frase = as_(args[1], " ".join(args[2:]))
    elif acao == "rotina" and len(args) >= 3:
        comando = "--comando" in args
        resto = [a for a in args[2:] if a != "--comando"]
        _, frase = rotina(args[1], " ".join(resto), comando)
    elif acao == "listar":
        itens = listar()
        frase = "\n".join(f"{d} — {q:%d/%m %H:%M}" if q else d for _, d, q in itens) or "nenhum lembrete nem rotina"
    elif acao == "cancelar" and len(args) >= 2:
        feitos = cancelar(" ".join(args[1:]))
        frase = "cancelado: " + "; ".join(feitos) if feitos else "nada parecido para cancelar"
    else:
        sys.exit(__doc__)
    print(frase)


if __name__ == "__main__":
    main()
