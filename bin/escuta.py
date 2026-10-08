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
import re
import shutil
import subprocess
import sys
import select
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
SILENCIO_FIM_S = 1.0         # silêncio depois da fala que encerra o pedido (no máximo)
SILENCIO_ESPECULA_S = 0.3    # com este silêncio o Parakeet já transcreve, enquanto o fim não chega
SILENCIO_COMPLETA_S = 0.6    # a frase parece completa: encerra com este silêncio, sem esperar o 1 s
ESPERA_FALA_S = 6.0          # acordou e ninguém falou: desiste
TENTATIVAS = 2               # falou só "Ei Jarvis" de novo (ou nada): ouve mais uma vez
MAX_PEDIDO_S = 15.0
CAUDA_DO_NOME_S = 1.5        # depois de acordar, a fala só conta como pedido depois do 1º silêncio
                             # (o fim do "Jarvis"); emendado sem pausa, conta depois deste tempo
CONTINUACAO_S = 5.0
FALA_MINIMA_CONTINUACAO_S = 0.5  # na continuação, menos voz que isso é ruído, não pedido
MAX_CONTINUACOES = 2             # respostas emendadas sem "Ei Jarvis": depois disso, só chamando de novo
SEM_SOM_S = 5.0              # o microfone manda 16 mil amostras por segundo: 5 s sem nada = travou
VOZ_LIMITE_S = 150           # um pedido (transcrever + Claude + falar) não passa disso
MIC_SEM_ECO = "jarvis_mic_sem_eco"  # serviço jarvis-aec: o microfone com a saída de som subtraída
# reconhecedor de fala dentro da escuta, já carregado: Parakeet v3 (sherpa-onnx). Medido em 08/10:
# 0,4 a 1 s por pedido, contra 3 a 4 s do Whisper small (que também errava mais: "Conta 7 vezes 8").
# JARVIS_STT=whisper volta ao caminho antigo (o voz.sh transcreve). O Insert segue com o Whisper
STT = os.environ.get("JARVIS_STT", "parakeet")
PARAKEET = os.path.expanduser("~/.local/share/jarvis/stt/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8")
PEDIDOS = os.path.join(EST, "pedidos")  # os últimos áudios de pedido (RAM): para comparar reconhecedores
GUARDAR_PEDIDOS = 30
INTERROMPER_S = 0.4          # você fala por isso enquanto ele fala: ele se cala e te ouve          # depois de uma resposta falada, ouve isso sem precisar de "Ei Jarvis"

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


class Gravacao:
    """Um pw-record que não trava: fechou ou ficou SEM_SOM_S sem mandar nada, reabre.
    Antes, a escuta saía "com sucesso" (e o serviço não voltava) ou esperava para sempre."""

    def __init__(self, alvo, nome):
        self.alvo, self.nome = alvo, nome
        self.proc = self._abrir()

    def _abrir(self):
        alvo = ["--target", self.alvo] if self.alvo else []
        return subprocess.Popen(["pw-record", "--rate", str(TAXA), "--channels", "1", "--format", "s16", *alvo, "-"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _ler_bytes(self, n):
        fd = self.proc.stdout.fileno()
        dados = b""
        while len(dados) < n:
            pronto, _, _ = select.select([fd], [], [], SEM_SOM_S)
            if not pronto:
                return None
            pedaco = os.read(fd, n - len(dados))
            if not pedaco:
                return None
            dados += pedaco
        return dados

    def ler(self):
        for tentativa in range(5):
            dados = self._ler_bytes(BLOCO * 2)
            if dados is not None:
                return np.frombuffer(dados, np.int16)
            log(f"microfone {self.nome} parou (pw-record: {self.proc.poll()}); reabrindo")
            self.proc.terminate()
            time.sleep(1 + tentativa)
            self.proc = self._abrir()
        raise RuntimeError(f"o microfone {self.nome} parou 5 vezes seguidas")

    def fechar(self):
        self.proc.terminate()


def carregar_stt():
    """O Parakeet pronto para usar, ou None (sem o modelo ou JARVIS_STT=whisper: o voz.sh transcreve)."""
    if STT != "parakeet" or not os.path.isdir(PARAKEET):
        return None
    try:
        import sherpa_onnx
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=f"{PARAKEET}/encoder.int8.onnx", decoder=f"{PARAKEET}/decoder.int8.onnx",
            joiner=f"{PARAKEET}/joiner.int8.onnx", tokens=f"{PARAKEET}/tokens.txt",
            num_threads=4, model_type="nemo_transducer")
    except Exception as e:
        log(f"Parakeet não carregou ({e}): sigo com o Whisper")
        return None


def ajustar_texto(texto):
    """O jeito do Parakeet escrever, trazido para o que o jarvis.py espera."""
    return re.sub(r"\bMr\.?(?=\s|$)", "MR", texto).strip()


# fim que pede continuação: "toca a música de…", "abre o…", "e…"
INCOMPLETA = re.compile(r"\b(e|ou|mas|de|da|do|das|dos|que|pra|para|com|o|a|os|as|um|uma|no|na|em|"
                        r"tipo|é|porque|quando|se|sobre|qual|quanto|me|meu|minha)$")


def parece_completa(texto):
    """A frase transcrita até a pausa parece acabada? Palavra só ("toca…") e fim em vírgula,
    artigo ou conjunção esperam o silêncio inteiro."""
    t = texto.strip().lower()
    if t.endswith((",", "-", "…")) or len(t.split()) < 2:
        return False
    return not INCOMPLETA.search(t.rstrip(" .!?"))


class Escuta:
    def __init__(self, teste=False):
        self.teste = teste
        self.modelo = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.modelo_limpo = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.vad = VAD()
        self.stt, self.trava_stt = carregar_stt(), threading.Lock()
        self.ativos, self.trava = 0, threading.Lock()  # voz.sh rodando (pode haver dois: interrupção)
        self.surdo_ate = 0.0
        self.de_novo = 0                   # tentativa seguinte, quando a fala foi só o nome
        self.continuar = False             # respondeu falando: ouvir a continuação
        self.continuacoes = 0              # quantas emendadas sem "Ei Jarvis"
        self.sem_eco = False
        self.voz_seguida = 0               # blocos seguidos com voz enquanto o Jarvis fala

    def _zerar(self):
        # o buffer interno dos detectores ainda tem o "Jarvis" de agora há pouco
        self.modelo.reset()
        self.modelo_limpo.reset()

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
        """Abre as duas gravações. A normal manda: detector e pedidos. A sem eco (serviço jarvis-aec)
        só ajuda: segundo detector e interrupção pela voz. Medido em 07/10: o filtro do microfone
        sem eco deformava a voz e o detector errava 3 de 5 "Ei Jarvis" — por isso ele não manda."""
        self.bruto = Gravacao(None, "normal")
        existe = subprocess.run(["pw-cli", "info", MIC_SEM_ECO], capture_output=True).returncode == 0
        self.limpo = Gravacao(MIC_SEM_ECO, "sem eco") if existe else None
        self.sem_eco = existe
        self.bloco_limpo = None
        log(f"microfone: normal{' + sem eco (jarvis-aec)' if existe else ' (jarvis-aec desligado)'}")
        return self.bruto

    def ler(self, mic=None):
        """Um bloco do microfone normal. O do sem eco é lido junto (fica em self.bloco_limpo):
        os dois andam no mesmo ritmo, e o cano de um não enche enquanto o outro é lido."""
        bloco = self.bruto.ler()
        if self.limpo:
            try:
                self.bloco_limpo = self.limpo.ler()
            except RuntimeError:
                log("microfone sem eco falhou de vez: sigo só com o normal")
                self.limpo.fechar()
                self.limpo, self.sem_eco, self.bloco_limpo = None, False, None
        return bloco

    def ouvir_pedido(self, mic, espera_s=ESPERA_FALA_S):
        """Grava do microfone até você parar de falar. Devolve as amostras, ou None se não falou."""
        blocos, falou, silencio, inicio = [], False, 0.0, time.time()
        nome_acabou = espera_s == CONTINUACAO_S  # na continuação não houve nome
        fala_s = 0.0
        calado = 0
        self.pico, self.vad_max = 0, 0.0  # diagnóstico do "ninguém falou"
        self.texto_pedido = None          # o que o Parakeet entendeu durante a pausa final
        especula = None                   # {"n": blocos transcritos, "texto": resultado}
        passo = BLOCO / TAXA
        folga = int(0.6 / passo)  # guarda 0,6 s antes da fala: com 0,3 s o "T" de "tocar" sumia ("Lócar")
        while True:
            bloco = self.ler(mic)
            blocos.append(bloco)
            # a voz é decidida pelo microfone sem eco quando existe: ele não ouve a música nem a voz
            # do Jarvis saindo pelas caixas, que antes viravam "pedido" (a letra da música transcrita)
            nota_vad = self.vad.predict(self.bloco_limpo if self.bloco_limpo is not None else bloco)
            self.pico, self.vad_max = max(self.pico, int(np.abs(bloco).max())), max(self.vad_max, nota_vad)
            voz = nota_vad > VAD_FALA
            # o resto do "Jarvis" não conta como pedido: antes ele marcava o início da fala, a pausa de
            # quem espera o sinal marcava o fim, e a gravação saía só com o nome ("A Jarvis"). O nome
            # acaba no primeiro silêncio de verdade (3 blocos: o VAD começa "frio" e o 1º bloco vem
            # baixo mesmo com fala) ou, tudo emendado, depois de CAUDA_DO_NOME_S
            emendado = False
            if not nome_acabou:
                calado = 0 if voz else calado + 1
                if calado >= 3:
                    nome_acabou = True
                elif time.time() - inicio >= CAUDA_DO_NOME_S:
                    nome_acabou, emendado = True, voz
                else:
                    voz = False
            if voz and not falou and not emendado:
                blocos = blocos[-(folga + 1):]  # o silêncio de espera só atrasaria o Whisper
            if voz:
                falou, silencio = True, 0.0
                fala_s += passo
                especula = None  # voltou a falar: a transcrição da pausa já não vale
            else:
                silencio += passo
            if falou and especula is None and silencio >= SILENCIO_ESPECULA_S and self.stt:
                especula = self.especular(blocos)
            completa = (especula is not None and especula.get("texto") is not None
                        and parece_completa(especula["texto"]))
            decorrido = time.time() - inicio
            if not falou and decorrido > espera_s:
                gravar_wav(np.concatenate(blocos[-int(espera_s / passo):]), os.path.join(EST, "ninguem.wav"))
                return None
            if falou and (silencio >= SILENCIO_FIM_S or (completa and silencio >= SILENCIO_COMPLETA_S)):
                if espera_s == CONTINUACAO_S and fala_s < FALA_MINIMA_CONTINUACAO_S:
                    return None  # na continuação, um "hum" ou um ruído curto não vira pedido
                self.fim_fala = time.time() - silencio
                if especula is not None:
                    especula["fio"].join()  # no fim de 1 s ela pode estar acabando
                    self.texto_pedido = especula.get("texto")
                log(f"fim de turno com {silencio:.1f} s de silêncio" + (" (frase completa)" if silencio < SILENCIO_FIM_S else ""))
                break
            if decorrido > MAX_PEDIDO_S:
                self.fim_fala = time.time()
                break
        return np.concatenate(blocos)

    def transcrever(self, amostras):
        """O texto do pedido pelo Parakeet, ou None se ele não está carregado (o voz.sh usa o Whisper)."""
        if not self.stt:
            return None
        with self.trava_stt:
            t = time.time()
            fluxo = self.stt.create_stream()
            fluxo.accept_waveform(TAXA, amostras.astype(np.float32) / 32768)
            self.stt.decode_stream(fluxo)
            texto = ajustar_texto(fluxo.result.text)
        log(f"parakeet ({time.time() - t:.2f} s): {texto}")
        return texto

    def especular(self, blocos):
        """Transcreve o que já foi dito numa thread, durante a pausa: quando o fim de turno chega,
        o texto está pronto (o Parakeet leva ~0,5 s, a pausa já ia esperar isso de qualquer jeito)."""
        e = {"texto": None}
        amostras = np.concatenate(blocos)

        def rodar():
            try:
                e["texto"] = self.transcrever(amostras)
            except Exception as erro:
                log(f"transcrição na pausa falhou: {erro}")
        e["fio"] = threading.Thread(target=rodar, daemon=True)
        e["fio"].start()
        return e

    def guardar_pedido(self, wav):
        """Cópia do áudio para comparar reconhecedores depois. Fica na RAM e só os últimos."""
        try:
            os.makedirs(PEDIDOS, exist_ok=True)
            shutil.copyfile(wav, os.path.join(PEDIDOS, os.path.basename(wav)))
            for velho in sorted(os.listdir(PEDIDOS))[:-GUARDAR_PEDIDOS]:
                os.remove(os.path.join(PEDIDOS, velho))
        except OSError:
            pass

    def entregar(self, amostras, tentativa, continuacao=False, texto=None):
        """Roda o voz.sh em paralelo: o laço principal precisa continuar drenando o microfone."""
        wav = os.path.join(EST, f"acordado-{time.time_ns()}.wav")  # dois pedidos juntos não se pisam
        inicio = time.time()
        # o falar.py mede daqui até o primeiro som da resposta (voz/latencia.log)
        env = {**os.environ, "JARVIS_FIM_FALA": f"{getattr(self, 'fim_fala', inicio):.3f}"}

        def rodar():
            try:
                gravar_wav(amostras, wav)
                self.guardar_pedido(wav)
                ja = texto if texto is not None else self.transcrever(amostras)
                pronto = [] if ja is None else ["--texto", ja]
                proc = subprocess.Popen([os.path.join(BIN, "voz.sh"), "--acordado", wav, *pronto],
                                        start_new_session=True, env=env)
                try:
                    proc.wait(timeout=VOZ_LIMITE_S)
                except subprocess.TimeoutExpired:
                    # pedido travado (clipboard com a tela bloqueada, Claude sem rede…): sem isto a
                    # escuta ficava "ocupada" para sempre e parava de ouvir
                    os.killpg(proc.pid, 15)
                    proc.wait()
                    log(f"voz.sh passou de {VOZ_LIMITE_S} s: encerrado")
                r = proc
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
        self.entregar(amostras, tentativa, continuacao, self.texto_pedido)

    def rodar(self):
        mic = self.microfone()
        acao.abaixar_musica.recuperar()  # se o serviço caiu com a música abaixada, devolve
        log("ouvindo \"Ei Jarvis\"")
        estado.marcar("parado")
        try:
            while True:
                bloco = self.ler(mic)
                nota = self.modelo.predict(bloco)["hey_jarvis"]
                if self.bloco_limpo is not None:  # vale a maior nota dos dois microfones
                    nota = max(nota, self.modelo_limpo.predict(self.bloco_limpo)["hey_jarvis"])
                if self.teste:
                    if nota > 0.05:
                        print(f"{time.strftime('%T')} nota {nota:.2f}" + ("  ← acordaria" if nota >= LIMIAR else ""),
                              flush=True)
                    continue
                agora = time.time()
                pid = self.falando()
                # sem eco, dá para interromper só falando: a voz dele não chega ao microfone
                if pid and self.sem_eco and nota < LIMIAR:
                    voz = self.bloco_limpo is not None and self.vad.predict(self.bloco_limpo) > VAD_FALA
                    self.voz_seguida = self.voz_seguida + 1 if voz else 0
                    if self.voz_seguida * BLOCO / TAXA >= INTERROMPER_S:
                        nota = 1.0  # trata como "Ei Jarvis": corta a fala e ouve
                        log("interrompido pela sua voz (sem eco)")
                else:
                    self.voz_seguida = 0
                pid = pid if nota >= LIMIAR else None
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
                    self._zerar()
                    continue
                if self.continuar and not self.ocupado():
                    self.continuar = False
                    e = acao.tocando()
                    if e and e["status"] == "Playing":
                        log("continuação pulada: tem música tocando")
                    elif self.continuacoes >= MAX_CONTINUACOES:
                        log("continuação pulada: já foram duas seguidas")
                    else:
                        self.continuacoes += 1
                        self.acordar(mic, continuacao=True)
                        self._zerar()
                    continue
                if self.de_novo and not self.ocupado():
                    tentativa, self.de_novo = self.de_novo, 0
                    log(f"só ouvi o nome: ouvindo de novo (tentativa {tentativa})")
                    self.acordar(mic, tentativa)
                    self._zerar()
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
                    self.continuacoes = 0  # chamou pelo nome: a conversa recomeça
                    log(f"acordei (nota {nota:.2f})")
                    self.acordar(mic)
                    self._zerar()  # o buffer interno ainda tem o "Jarvis" de agora há pouco
        except KeyboardInterrupt:
            pass
        except Exception:
            # qualquer erro fica no log e o processo sai com falha: o systemd reinicia em 5 s
            import traceback
            log("erro, reiniciando:\n" + traceback.format_exc())
            raise SystemExit(1)
        finally:
            self.bruto.fechar()
            if self.limpo:
                self.limpo.fechar()


if __name__ == "__main__":
    Escuta(teste="--teste" in sys.argv).rodar()
