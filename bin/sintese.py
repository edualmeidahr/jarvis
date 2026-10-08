#!/usr/bin/env python3
"""A voz do Jarvis já carregada: o Piper fica aberto e o timbre roda aqui dentro.

Serviço jarvis-fala (systemd --user), no venv do Jarvis (o timbre precisa do numpy). O falar.py
manda cada frase pelo socket $XDG_RUNTIME_DIR/jarvis/fala.sock e recebe o WAV pronto. Sem o
serviço, o falar.py faz como antes: um Piper e um timbre.py por frase.

Medido em 08/10: abrir o Piper custava ~0,4 s por frase, e o timbre.py ~0,25 s (subir o Python
e importar o numpy). Com o Piper aberto, "Ok." sai em 45 ms. O som é o mesmo: mesmos
parâmetros do falar.py e a mesma função do timbre.py.

    sintese.py          serve até ser parado
"""
import importlib.util
import json
import os
import select
import socket
import subprocess
import tempfile
import threading
import time
import wave

import numpy as np

BIN = os.path.dirname(os.path.realpath(__file__))
SOCKET = os.path.join(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()), "jarvis", "fala.sock")
LIMITE_S = 20  # uma frase não leva isso; passou, o Piper travou e é reaberto


def _modulo(nome):
    spec = importlib.util.spec_from_file_location(nome, os.path.join(BIN, f"{nome}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


falar = _modulo("falar")    # os parâmetros da voz moram lá: uma fonte só
timbre = _modulo("timbre")
try:
    ingles = _modulo("ingles")  # trechos em inglês com sotaque brasileiro (precisa do piper-tts e do wordfreq)
except Exception:
    ingles = None


class PiperPython:
    """A mesma voz pelo piper-tts (Python), que aceita fonemas no meio do texto ([[ ... ]]): é o que
    deixa falar "issue" e "Highway to Hell" em inglês. Mesmos parâmetros do Piper de linha de comando."""

    def __init__(self):
        from piper import PiperVoice, SynthesisConfig
        self.voz = PiperVoice.load(falar.VOZ)
        self.config = SynthesisConfig(length_scale=falar.VELOCIDADE * falar.TOM, noise_scale=float(falar.TIMBRE),
                                      noise_w_scale=float(falar.RITMO))
        self.pausa = float(falar.PAUSA)

    def sintetizar(self, texto, saida):
        if ingles:
            try:
                texto = ingles.marcar(texto)
            except Exception:
                pass  # sem o inglês, a frase sai como antes
        taxa, partes = self.voz.config.sample_rate, []
        silencio = bytes(2 * int(taxa * self.pausa))
        for pedaco in self.voz.synthesize(texto, self.config):
            partes += [pedaco.audio_int16_bytes, silencio]  # a pausa entre frases do --sentence_silence
        if not partes:
            return False
        with wave.open(saida, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(taxa)
            w.writeframes(b"".join(partes))
        return True


def abrir_voz(pasta):
    """O piper-tts quando dá (com inglês); senão o Piper de linha de comando, como antes."""
    try:
        return PiperPython()
    except Exception as e:
        print(f"piper-tts indisponível ({e}): sigo com o Piper de linha de comando", flush=True)
        return Piper(pasta)


class Piper:
    """Um Piper aberto em --json-input: uma linha {"text", "output_file"} entra, o caminho sai."""

    def __init__(self, pasta):
        self.pasta, self.proc = pasta, None

    def _abrir(self):
        self.proc = subprocess.Popen(
            [falar.PIPER, "--model", falar.VOZ, "--json-input", "--output_dir", self.pasta,
             "--sentence_silence", falar.PAUSA, "--noise_w", falar.RITMO, "--noise_scale", falar.TIMBRE,
             "--length_scale", f"{falar.VELOCIDADE * falar.TOM:.3f}"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def sintetizar(self, texto, saida):
        if self.proc is None or self.proc.poll() is not None:
            self._abrir()
        self.proc.stdin.write(json.dumps({"text": texto, "output_file": saida}, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        pronto, _, _ = select.select([self.proc.stdout], [], [], LIMITE_S)
        linha = self.proc.stdout.readline() if pronto else ""
        if not linha.strip():
            self.proc.kill()
            self.proc = None
            return False
        return os.path.exists(saida)


def aplicar_timbre(cru, saida, primeira):
    """O mesmo que o timbre.py faz, sem subir outro Python."""
    with wave.open(cru) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
    y = timbre.processar(x, sr, folga=primeira)
    with wave.open(saida, "wb") as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(int(sr * timbre.TOM))
        o.writeframes(y.astype(np.int16).tobytes())


def servir():
    pasta = tempfile.mkdtemp(prefix="jarvis-fala-", dir=os.path.dirname(SOCKET))
    piper, vez = abrir_voz(pasta), threading.Lock()
    with vez:  # aquece: a primeira frase não paga a carga do modelo
        piper.sintetizar("Pronto.", os.path.join(pasta, "aquece.wav"))
    try:
        os.remove(SOCKET)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET)
    os.chmod(SOCKET, 0o600)
    srv.listen(4)

    def atender(con):
        with con:
            try:
                pedido = json.loads(con.makefile().readline())
                cru = os.path.join(pasta, f"{time.time_ns()}-cru.wav")
                with vez:
                    ok = piper.sintetizar(pedido["texto"], cru)
                if ok:
                    aplicar_timbre(cru, pedido["saida"], bool(pedido.get("primeira")))
                    os.remove(cru)
                resposta = {"ok": ok}
            except Exception as e:  # uma frase ruim não derruba o serviço
                resposta = {"ok": False, "erro": str(e)}
            try:
                con.sendall(json.dumps(resposta).encode() + b"\n")
            except OSError:
                pass

    while True:
        con, _ = srv.accept()
        threading.Thread(target=atender, args=(con,), daemon=True).start()


if __name__ == "__main__":
    servir()
