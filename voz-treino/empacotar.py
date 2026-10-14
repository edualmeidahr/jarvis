#!/usr/bin/env python3
"""Confere o áudio gerado e monta o zip do treino (para subir no Google Drive).

    ~/.local/share/jarvis/venv/bin/python voz-treino/empacotar.py

Conferência: cada WAV é transcrito pelo Parakeet e comparado com o texto pedido. Voz sintética às
vezes troca, corta ou inventa palavra; um áudio assim ensina errado e estraga a voz, então sai do
treino (fica listado em revisar.txt). Áudio curto demais, longo demais ou mudo também sai.

Saída: ~/.local/share/jarvis/voz-treino/jarvis-dataset.zip
    metadata.csv   "<id>.wav|texto" (o formato que o piper.train lê)
    wavs/<id>.wav  22050 Hz, mono, 16 bits (a taxa da voz Faber, de onde o treino parte)
"""
import difflib
import os
import re
import subprocess
import sys
import unicodedata
import wave
import zipfile

import numpy as np

PASTA = os.path.expanduser("~/.local/share/jarvis/voz-treino")
WAVS = os.path.join(PASTA, "wavs")
PRONTOS = os.path.join(PASTA, "wavs-22k")
PARAKEET = os.path.expanduser("~/.local/share/jarvis/stt/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8")
NOTA_MINIMA = 0.75  # parecido do texto transcrito com o pedido


def normalizar(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ler(caminho):
    with wave.open(caminho) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768, w.getframerate()


def main():
    import sherpa_onnx
    stt = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=f"{PARAKEET}/encoder.int8.onnx", decoder=f"{PARAKEET}/decoder.int8.onnx",
        joiner=f"{PARAKEET}/joiner.int8.onnx", tokens=f"{PARAKEET}/tokens.txt",
        num_threads=4, model_type="nemo_transducer")
    ff = ffmpeg()
    os.makedirs(PRONTOS, exist_ok=True)
    linhas = [l.rstrip("\n").split("|", 1) for l in open(os.path.join(PASTA, "metadata.csv"), encoding="utf-8")]
    bons, ruins, total_s = [], [], 0.0
    for fid, texto in linhas:
        origem, destino = os.path.join(WAVS, f"{fid}.wav"), os.path.join(PRONTOS, f"{fid}.wav")
        if not os.path.exists(destino):
            subprocess.run([ff, "-loglevel", "error", "-y", "-i", origem, "-ac", "1", "-ar", "22050",
                            "-sample_fmt", "s16", destino], check=True)
        audio, taxa = ler(destino)
        dur = len(audio) / taxa
        fluxo = stt.create_stream()
        # o Parakeet quer 16 kHz: reamostragem simples, só para conferir
        idx = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / taxa))
        fluxo.accept_waveform(16000, np.interp(idx, np.arange(len(audio)), audio).astype(np.float32))
        stt.decode_stream(fluxo)
        ouvido = fluxo.result.text
        nota = difflib.SequenceMatcher(None, normalizar(texto), normalizar(ouvido)).ratio()
        palavras = len(texto.split())
        motivo = ("mudo" if np.abs(audio).max() < 0.05 else
                  "curto demais" if dur < 0.5 else
                  "longo demais" if dur > 20 else
                  "fala lenta ou com sobra" if dur > 1.5 + palavras * 0.9 else
                  f"texto diferente ({nota:.2f})" if nota < NOTA_MINIMA and palavras > 1 else "")
        if motivo:
            ruins.append(f"{fid}\t{motivo}\tpedido: {texto}\touvido: {ouvido}")
        else:
            bons.append((fid, texto))
            total_s += dur
    with open(os.path.join(PASTA, "revisar.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(ruins) + "\n")
    zipado = os.path.join(PASTA, "jarvis-dataset.zip")
    with zipfile.ZipFile(zipado, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("metadata.csv", "".join(f"{fid}.wav|{texto}\n" for fid, texto in bons))
        for fid, _ in bons:
            z.write(os.path.join(PRONTOS, f"{fid}.wav"), f"wavs/{fid}.wav")
    print(f"{len(bons)} frases boas ({total_s / 60:.1f} min de fala), {len(ruins)} fora (revisar.txt)")
    print(f"zip: {zipado} ({os.path.getsize(zipado) / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
