#!/usr/bin/env python3
"""Gera o áudio de treino da voz: cada frase de frases.txt falada pela voz "Jarvis (UCM) - Português
Brasileiro" do Fish Audio. Roda uma vez; depois a voz é treinada (Piper) e roda offline.

    voz-treino/gerar.py            gera o que falta (pode parar e rodar de novo: pula o que já existe)
    voz-treino/gerar.py --teste    só as 3 primeiras frases

Saída (fora do repositório: é a voz de um dublador, uso pessoal):
    ~/.local/share/jarvis/voz-treino/wavs/<id>.wav
    ~/.local/share/jarvis/voz-treino/metadata.csv    "id|texto", o formato LJSpeech que o Piper treina

Chave da API no chaveiro: secret-tool lookup service jarvis-fish account api
Modelo s2.1-pro-free (grátis, "uso justo"): um pedido por vez, com pausa entre eles. Se a API
começar a cobrar ou recusar (402/403), para e avisa, sem insistir.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.realpath(__file__))
FRASES = os.path.join(AQUI, "frases.txt")
SAIDA = os.path.expanduser("~/.local/share/jarvis/voz-treino")
WAVS = os.path.join(SAIDA, "wavs")
LOG = os.path.join(SAIDA, "gerar.log")
API = "https://api.fish.audio/v1/tts"
MODELO = "s2.1-pro-free"
VOZ = "a5b93aeddcc948c19ea04f0afe9d178c"  # Jarvis (UCM) - Português Brasileiro
PAUSA_S = 3.0


def log(msg):
    linha = f"{time.strftime('%F %T')} {msg}"
    print(linha, flush=True)
    with open(LOG, "a") as f:
        f.write(linha + "\n")


def chave():
    r = subprocess.run([os.path.expanduser("~/.local/bin/secret-tool"), "lookup", "service", "jarvis-fish",
                        "account", "api"], capture_output=True, text=True)
    if not r.stdout.strip():
        sys.exit("Sem a chave do Fish Audio no chaveiro (service jarvis-fish account api).")
    return r.stdout.strip()


def frases():
    for linha in open(FRASES, encoding="utf-8"):
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            yield hashlib.sha1(linha.encode()).hexdigest()[:10], linha


def falar(texto, k):
    corpo = json.dumps({"text": texto, "reference_id": VOZ, "format": "wav", "normalize": True}).encode()
    req = urllib.request.Request(API, data=corpo, headers={
        "Authorization": f"Bearer {k}", "Content-Type": "application/json", "model": MODELO})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main():
    teste = "--teste" in sys.argv
    os.makedirs(WAVS, exist_ok=True)
    k = chave()
    todas = list(frases())[:3] if teste else list(frases())
    feitas = 0
    for n, (fid, texto) in enumerate(todas, 1):
        destino = os.path.join(WAVS, f"{fid}.wav")
        if os.path.exists(destino):
            continue
        for tentativa in range(5):
            try:
                audio = falar(texto, k)
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 402, 403):
                    log(f"a API recusou ({e.code}: {e.read()[:200]!r}): parei. Feitas nesta rodada: {feitas}")
                    return 1
                espera = 30 * (tentativa + 1)
                log(f"{fid}: HTTP {e.code}, espero {espera} s")
                time.sleep(espera)
            except (OSError, TimeoutError) as e:
                log(f"{fid}: {e}, espero 30 s")
                time.sleep(30)
        else:
            log(f"{fid}: desisti depois de 5 tentativas")
            continue
        if not audio.startswith(b"RIFF") or len(audio) < 5000:
            log(f"{fid}: resposta não é áudio ({len(audio)} bytes), pulei")
            continue
        with open(destino + ".tmp", "wb") as f:
            f.write(audio)
        os.replace(destino + ".tmp", destino)
        feitas += 1
        if feitas % 10 == 0 or teste:
            log(f"{n}/{len(todas)} prontas")
        time.sleep(PAUSA_S)
    # o metadata sai sempre inteiro, só com as frases que têm áudio
    with open(os.path.join(SAIDA, "metadata.csv"), "w", encoding="utf-8") as f:
        for fid, texto in frases():
            if os.path.exists(os.path.join(WAVS, f"{fid}.wav")):
                f.write(f"{fid}|{texto}\n")
    log(f"fim: {feitas} novas nesta rodada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
