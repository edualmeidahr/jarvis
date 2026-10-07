#!/usr/bin/env python3
"""Fica ouvindo o microfone e acorda o Jarvis quando você diz "Ei Jarvis".

Roda como serviço (systemd --user jarvis-escuta). Liga e desliga com Ctrl+Alt+J
(escuta-alterna.sh). O Insert continua funcionando igual, ao lado.

    escuta.py            ouve até ser parado
    escuta.py --teste    mostra a pontuação do detector em tempo real, sem acionar nada

Como funciona:
1. pw-record entrega o microfone cru (16 kHz, mono) num pipe.
2. openWakeWord dá uma nota de 0 a 1 para "hey jarvis" a cada 80 ms. O modelo é o
   pronto do projeto: "Jarvis" sozinho não acorda (testado: nota ~0), "Ei Jarvis" sim.
3. Acordou: som de "ouvindo", a música baixa, e o Silero VAD (detector de voz) decide
   quando você terminou de falar — 1 s de silêncio depois da fala.
4. O áudio vai para o `voz.sh --acordado`, que transcreve com o Whisper e entrega ao
   Jarvis. Enquanto ele trabalha e fala, o detector fica surdo (para não se ouvir).

Nada sai da máquina: detector, VAD e Whisper rodam local.
"""
import importlib.util
import os
import subprocess
import sys
import threading
import time
import wave

import numpy as np
from openwakeword.model import Model
from openwakeword.vad import VAD

BIN = os.path.expanduser("~/.claude/bin")
EST = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "voz")
WAV = os.path.join(EST, "acordado.wav")
PID_INSERT = os.path.join(EST, "pid")  # gravação do Insert em curso

TAXA = 16000
BLOCO = 1280                 # 80 ms: o passo do openWakeWord
LIMIAR = 0.5                 # nota mínima para acordar; suba se acordar sozinho, desça se não te ouvir
FOLGA_S = 2.0                # depois de acordar, ignora o detector por esse tempo
VAD_FALA = 0.5               # acima disso, o bloco tem voz
SILENCIO_FIM_S = 1.0         # silêncio depois da fala que encerra o pedido
ESPERA_FALA_S = 6.0          # acordou e ninguém falou: desiste
TENTATIVAS = 2               # falou só "Ei Jarvis" de novo (ou nada): ouve mais uma vez
MAX_PEDIDO_S = 15.0
CONTINUACAO_S = 5.0          # depois de uma resposta falada, ouve isso sem precisar de "Ei Jarvis"

# marcas trocadas com o jarvis.py e o falar.py
JV = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis")
FALANDO = os.path.join(JV, "falando.pid")       # o falar.py escreve o pid do pw-play enquanto fala
INTERROMPIDO = os.path.join(JV, "interrompido")  # o processo do jarvis.py que for interrompido se cala
CONTINUAR = os.path.join(JV, "continuar")        # o jarvis.py respondeu falando: vale ouvir a continuação

_spec_e = importlib.util.spec_from_file_location("estado", os.path.join(BIN, "estado.py"))
estado = importlib.util.module_from_spec(_spec_e)
_spec_e.loader.exec_module(estado)  # o ponto colorido na barra do GNOME

_spec = importlib.util.spec_from_file_location("acao", os.path.join(BIN, "acao.py"))
acao = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(acao)


LOG = os.path.join(EST, "escuta.log")


def log(msg):
    """O journal do usuário não abre sem o grupo systemd-journal: o rastro fica aqui."""
    os.makedirs(EST, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%F %T')} {msg}\n")


def bolha(*args):
    subprocess.run([sys.executable, os.path.join(BIN, "bolha.py"), *args], check=False)


def gravar_wav(amostras, caminho=WAV):
    os.makedirs(EST, exist_ok=True)
    with wave.open(caminho, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAXA)
        w.writeframes(amostras.astype(np.int16).tobytes())


class Escuta:
    def __init__(self, teste=False):
        self.teste = teste
        self.modelo = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.vad = VAD()
        self.ativos, self.trava = 0, threading.Lock()  # voz.sh rodando (pode haver dois: interrupção)
        self.surdo_ate = 0.0
        self.de_novo = 0                   # tentativa seguinte, quando a fala foi só o nome
        self.continuar = False             # respondeu falando: ouvir a continuação

    def ocupado(self):
        return self.ativos > 0

    def falando(self):
        """O pid do pw-play do Jarvis, se ele está falando agora."""
        try:
            pid = int(open(FALANDO).read())
            os.kill(pid, 0)
            return pid
        except (OSError, ValueError):
            return None

    def microfone(self):
        return subprocess.Popen(["pw-record", "--rate", str(TAXA), "--channels", "1", "--format", "s16", "-"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def ler(self, mic):
        dados = mic.stdout.read(BLOCO * 2)
        if len(dados) < BLOCO * 2:
            raise EOFError
        return np.frombuffer(dados, np.int16)

    def ouvir_pedido(self, mic, espera_s=ESPERA_FALA_S):
        """Grava do microfone até você parar de falar. Devolve as amostras, ou None se não falou."""
        blocos, falou, silencio, inicio = [], False, 0.0, time.time()
        self.pico, self.vad_max = 0, 0.0  # diagnóstico do "ninguém falou"
        passo = BLOCO / TAXA
        folga = int(0.6 / passo)  # guarda 0,6 s antes da fala: com 0,3 s o "T" de "tocar" sumia ("Lócar")
        while True:
            bloco = self.ler(mic)
            blocos.append(bloco)
            nota_vad = self.vad.predict(bloco)
            self.pico, self.vad_max = max(self.pico, int(np.abs(bloco).max())), max(self.vad_max, nota_vad)
            voz = nota_vad > VAD_FALA
            if voz and not falou:
                blocos = blocos[-(folga + 1):]  # o silêncio de espera só atrasaria o Whisper
            if voz:
                falou, silencio = True, 0.0
            else:
                silencio += passo
            decorrido = time.time() - inicio
            if not falou and decorrido > espera_s:
                gravar_wav(np.concatenate(blocos[-int(espera_s / passo):]), os.path.join(EST, "ninguem.wav"))
                return None
            if falou and silencio >= SILENCIO_FIM_S:
                break
            if decorrido > MAX_PEDIDO_S:
                break
        return np.concatenate(blocos)

    def entregar(self, amostras, tentativa, continuacao=False):
        """Roda o voz.sh em paralelo: o laço principal precisa continuar drenando o microfone."""
        wav = os.path.join(EST, f"acordado-{time.time_ns()}.wav")  # dois pedidos juntos não se pisam
        inicio = time.time()

        def rodar():
            try:
                gravar_wav(amostras, wav)
                r = subprocess.run([os.path.join(BIN, "voz.sh"), "--acordado", wav], check=False)
                log(f"voz.sh terminou com {r.returncode}")
                interrompido = os.path.exists(INTERROMPIDO) and os.path.getmtime(INTERROMPIDO) > inicio
                if r.returncode == 20 and continuacao:
                    pass  # na continuação, silêncio ou só o nome: a conversa acabou, sem aviso
                elif r.returncode == 20 and tentativa < TENTATIVAS:
                    self.de_novo = tentativa + 1  # o laço principal ouve de novo
                elif r.returncode == 20:
                    bolha("--fim", "Não entendi o pedido.", "Pode chamar de novo quando quiser.")
                elif (r.returncode == 0 and not interrompido and os.path.exists(CONTINUAR)
                      and os.path.getmtime(CONTINUAR) > inicio):
                    self.continuar = True  # respondeu falando: ouve a continuação
            finally:
                try:
                    os.remove(wav)
                except OSError:
                    pass
                self.surdo_ate = time.time() + FOLGA_S
                with self.trava:
                    self.ativos -= 1
                    if not self.ativos and not self.continuar:
                        estado.marcar("parado")
        with self.trava:
            self.ativos += 1
        threading.Thread(target=rodar, daemon=True).start()

    def acordar(self, mic, tentativa=1, continuacao=False):
        if continuacao:
            bolha("--nova", "Pode continuar…", "Ou diga “obrigado”.")
        elif tentativa == 1:
            bolha("--nova", "--som", "ouvindo", "Estou ouvindo…", "Pode falar.")
        else:
            bolha("--som", "ouvindo", "Pode falar…", "Estou te ouvindo.")
        estado.marcar("ouvindo", "continuação" if continuacao else "")
        with acao.abaixar_musica(0.15):  # com a música alta, o VAD nunca ouve o silêncio do fim
            amostras = self.ouvir_pedido(mic, CONTINUACAO_S if continuacao else ESPERA_FALA_S)
        estado.marcar("pensando" if amostras is not None else "parado")
        if amostras is None and continuacao:
            log("continuação: ninguém falou, conversa encerrada")
            self.surdo_ate = time.time() + FOLGA_S
            return
        if amostras is None:
            vol = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"], capture_output=True, text=True).stdout.strip()
            log(f"acordei, mas ninguém falou (pico {self.pico}/32767, VAD máx {self.vad_max:.2f}, microfone {vol})")
            bolha("--fim", "Não ouvi nada.", "Pode chamar de novo quando quiser.")
            self.surdo_ate = time.time() + FOLGA_S
            return
        log(f"pedido gravado: {len(amostras) / TAXA:.1f} s" + (" (continuação)" if continuacao else ""))
        self.entregar(amostras, tentativa, continuacao)

    def rodar(self):
        mic = self.microfone()
        acao.abaixar_musica.recuperar()  # se o serviço caiu com a música abaixada, devolve
        log("ouvindo \"Ei Jarvis\"")
        estado.marcar("parado")
        try:
            while True:
                bloco = self.ler(mic)
                nota = self.modelo.predict(bloco)["hey_jarvis"]
                if self.teste:
                    if nota > 0.05:
                        print(f"{time.strftime('%T')} nota {nota:.2f}" + ("  ← acordaria" if nota >= LIMIAR else ""),
                              flush=True)
                    continue
                agora = time.time()
                pid = self.falando() if nota >= LIMIAR else None
                if pid:
                    # "Ei Jarvis" com ele falando: corta a fala e ouve o pedido novo
                    os.makedirs(JV, exist_ok=True)
                    open(INTERROMPIDO, "w").close()
                    try:
                        os.kill(pid, 15)
                    except OSError:
                        pass
                    self.continuar, self.de_novo = False, 0
                    log(f"interrompido (nota {nota:.2f})")
                    self.acordar(mic)
                    self.modelo.reset()
                    continue
                if self.continuar and not self.ocupado():
                    self.continuar = False
                    self.acordar(mic, continuacao=True)
                    self.modelo.reset()
                    continue
                if self.de_novo and not self.ocupado():
                    tentativa, self.de_novo = self.de_novo, 0
                    log(f"só ouvi o nome: ouvindo de novo (tentativa {tentativa})")
                    self.acordar(mic, tentativa)
                    self.modelo.reset()
                    continue
                if self.ocupado() or agora < self.surdo_ate:
                    continue
                if nota >= LIMIAR and os.path.exists(PID_INSERT):
                    # "Ei Jarvis" com o Insert gravando: a gravação foi esquecida. A voz ganha
                    log("Insert estava gravando: cancelei para atender o \"Ei Jarvis\"")
                    subprocess.run([os.path.join(BIN, "voz.sh"), "cancela"], check=False)
                elif os.path.exists(PID_INSERT):
                    continue
                if nota >= LIMIAR:
                    log(f"acordei (nota {nota:.2f})")
                    self.acordar(mic)
                    self.modelo.reset()  # o buffer interno ainda tem o "Jarvis" de agora há pouco
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            mic.terminate()


if __name__ == "__main__":
    Escuta(teste="--teste" in sys.argv).rodar()
