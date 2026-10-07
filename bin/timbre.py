#!/usr/bin/env python3
"""Dá corpo à voz do Jarvis: o WAV do Piper entra cru e sai mais grave, cheio e firme.

    timbre.py ENTRADA.wav SAIDA.wav

Escolhido de ouvido em 07/10 (a "Seis C, mais rápida"), entre variações da Faber. O tom
desce pela taxa de amostragem: o falar.py já pede ao Piper uma fala TOM vezes mais curta,
e tocar na taxa menor devolve a duração e engrossa a voz. Mais de ~10% soa robótico.
Roda no venv do Jarvis porque o Python do sistema não tem numpy. Só numpy, de propósito:
importar o scipy levava 1 s, e a voz atrasava isso a cada fala. Os filtros são feitos no
espectro (FFT), com as mesmas curvas de Butterworth.
"""
import sys
import wave

import numpy as np

TOM = 0.92        # 8% mais grave; o falar.py usa o mesmo valor no length_scale
GRAVES = 1.1      # corpo: reforço do médio-grave (120–400 Hz) e um toque de peito
LIMIAR = 0.25     # compressão: o que passa disso é abaixado...
RAZAO = 3.0       # ...nesta proporção. Voz mais cheia e "na frente", como locução
SALA = ((0.023, 0.07), (0.041, 0.05))  # (atraso s, volume): ambiente bem discreto
FOLGA_S = 0.35    # silêncio na frente: a placa de som acorda sem comer a primeira sílaba


def _passa_baixa(f, corte, ordem):
    return 1 / np.sqrt(1 + (f / corte) ** (2 * ordem))


def _suavizar(env, sr, corte=25.0):
    """Média exponencial (passa-baixa de 1ª ordem) feita por convolução na FFT: sem laço em Python."""
    tau = 1 / (2 * np.pi * corte)
    k = np.exp(-np.arange(int(sr * tau * 6)) / (sr * tau))
    k /= k.sum()
    n = len(env) + len(k) - 1
    return np.fft.irfft(np.fft.rfft(env, n) * np.fft.rfft(k, n), n)[: len(env)]


def processar(x, sr):
    f = np.fft.rfftfreq(len(x), 1 / sr)
    f[0] = 1e-3
    banda = _passa_baixa(f, 400, 2) * (1 - _passa_baixa(f, 120, 2))  # 120–400 Hz: corpo
    ganho = 1 + GRAVES * banda + 0.3 * GRAVES * _passa_baixa(f, 120, 2)  # + peito
    y = np.fft.irfft(np.fft.rfft(x) * ganho, len(x))
    env = np.abs(y) / max(1e-9, np.abs(y).max())
    env = np.maximum(_suavizar(env, sr), 1e-6)
    y *= np.where(env > LIMIAR, (env / LIMIAR) ** (1 / RAZAO - 1), 1.0)
    for atraso, ganho in SALA:
        d = int(sr * atraso)
        y[d:] += ganho * y[:-d]
    y = y / max(1.0, np.abs(y).max() / 29000)
    return np.concatenate([np.zeros(int(sr * FOLGA_S)), y])


def main():
    entrada, saida = sys.argv[1], sys.argv[2]
    with wave.open(entrada) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
    y = processar(x, sr)
    with wave.open(saida, "wb") as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(int(sr * TOM))
        o.writeframes(y.astype(np.int16).tobytes())


if __name__ == "__main__":
    main()
