#!/usr/bin/env python3
"""A bolha do Jarvis: uma notificação só por conversa, que muda conforme ela anda.

    bolha.py "Estou ouvindo…"                       passageira (não fica no histórico)
    bolha.py --som ouvindo "Estou ouvindo…"         com o som de começar a ouvir
    bolha.py --fim "Pronto para colar" "“texto”"    a última: fica no histórico
    bolha.py --nova --fim "título" "corpo"          começa outra bolha (atalho, timer)

Antes, cada etapa era uma notificação nova e elas empilhavam: o voz.sh pedia para
substituir com a dica do notify-osd (x-canonical-private-synchronous), que o GNOME
ignora. Aqui a troca é pelo id (`notify-send -r`), que o GNOME respeita.

Também importável: `bolha.mostrar(titulo, corpo, fim=False, som=None)`.
"""
import math
import os
import struct
import subprocess
import sys
import time
import wave

# o WirePlumber memoriza o volume de cada tipo de stream e aplica no próximo. Um som do Jarvis
# que terminou abaixado (ducking) deixava todos os seguintes mudos — aconteceu duas vezes.
# Com esta propriedade, os sons dele sempre nascem em 100%
SEM_VOLUME_SALVO = ["-P", "{ state.restore-props = false }"]

ESTADO = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis")
ID = os.path.join(ESTADO, "bolha.id")
DADOS = os.path.expanduser("~/.local/share/jarvis")
ICONE = os.path.join(DADOS, "jarvis.svg")
VALIDADE_S = 120  # bolha mais velha que isso é de outra conversa: começa uma nova

# dois toques curtos e baixos, subindo para "ouvindo" e descendo para "entendi"
SONS = {"ouvindo": (660, 990), "entendi": (990, 660)}
VOLUME = 0.35  # 0.18 passava despercebido: ele acordava e você não notava

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs><radialGradient id="g" cx="50%" cy="50%" r="50%">
    <stop offset="0" stop-color="#E9FBFF"/><stop offset="0.35" stop-color="#5FD4F4"/>
    <stop offset="1" stop-color="#0B4F6C" stop-opacity="0"/></radialGradient></defs>
  <circle cx="32" cy="32" r="30" fill="url(#g)"/>
  <circle cx="32" cy="32" r="19" fill="none" stroke="#7FE3FF" stroke-width="2.5" opacity="0.9"/>
  <circle cx="32" cy="32" r="25" fill="none" stroke="#3BB7DE" stroke-width="1.5"
          stroke-dasharray="6 5" opacity="0.8"/>
  <circle cx="32" cy="32" r="7" fill="#F2FDFF"/>
</svg>
"""


def _preparar():
    """Ícone e sons nascem na primeira vez; nada para instalar."""
    os.makedirs(DADOS, exist_ok=True)
    if not os.path.exists(ICONE):
        with open(ICONE, "w") as f:
            f.write(SVG)
    for nome, (a, b) in SONS.items():
        caminho = os.path.join(DADOS, f"{nome}.wav")
        if os.path.exists(caminho):
            continue
        taxa, nota = 44100, 0.12
        # silêncio na frente: a placa de som fica suspensa depois de 5 s parada (PipeWire) e,
        # ao acordar, engole o começo do áudio. Sem isso, o toque inteiro sumia
        amostras = [0.0] * int(taxa * 0.35)
        for freq in (a, b):
            n = int(taxa * nota)
            for i in range(n):
                env = math.sin(math.pi * i / n) ** 2  # sobe e desce suave: sem estalo
                amostras.append(VOLUME * env * math.sin(2 * math.pi * freq * i / taxa))
        with wave.open(caminho, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(taxa)
            w.writeframes(b"".join(struct.pack("<h", int(x * 32767)) for x in amostras))


def nao_perturbe():
    r = subprocess.run(["gsettings", "get", "org.gnome.desktop.notifications", "show-banners"],
                       capture_output=True, text=True)
    return r.stdout.strip() == "false"


def som(nome):
    caminho = os.path.join(DADOS, f"{nome}.wav")
    if os.path.exists(caminho) and not nao_perturbe():
        subprocess.Popen(["pw-play", *SEM_VOLUME_SALVO, caminho], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


def _id_atual():
    try:
        if time.time() - os.path.getmtime(ID) < VALIDADE_S:
            return open(ID).read().strip()
    except OSError:
        pass
    return None


def mostrar(titulo, corpo="", fim=False, som_=None, nova=False):
    _preparar()
    if som_:
        som(som_)
    cmd = ["notify-send", "-a", "Jarvis", "-i", ICONE, "-p"]
    anterior = None if nova else _id_atual()
    if anterior:
        cmd += ["-r", anterior]
    if not fim:
        # passageira: some sozinha e não suja o histórico. Sem "-u low": o GNOME pode
        # mandar urgência baixa direto para a bandeja, sem banner — e aí você não vê que ele acordou
        cmd += ["-e"]
    cmd += [titulo, corpo]
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.makedirs(ESTADO, exist_ok=True)
    if fim:
        # a última fala da conversa: a próxima começa bolha nova, e esta fica no histórico
        try:
            os.remove(ID)
        except OSError:
            pass
    elif r.stdout.strip():
        with open(ID, "w") as f:
            f.write(r.stdout.strip())


def aspas(texto, limite=140):
    texto = " ".join(texto.split())
    return f"“{texto[:limite].rstrip()}{'…' if len(texto) > limite else ''}”"


def main():
    args = sys.argv[1:]
    fim = "--fim" in args
    nova = "--nova" in args
    som_ = None
    if "--som" in args:
        i = args.index("--som")
        som_ = args[i + 1]
        del args[i:i + 2]
    args = [a for a in args if a not in ("--fim", "--nova")]
    if not args:
        sys.exit(__doc__)
    mostrar(args[0], args[1] if len(args) > 1 else "", fim=fim, som_=som_, nova=nova)


if __name__ == "__main__":
    main()
