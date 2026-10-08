#!/usr/bin/env python3
"""O cérebro do Jarvis: uma sessão do Claude sempre aberta, para não pagar a partida a cada pedido.

    cerebro.py              serviço (systemd --user jarvis-cerebro)
    cerebro.py "pergunta"   manda uma pergunta ao serviço e imprime a resposta (teste)

Medido em 08/10: ligar o `claude -p` leva ~3 s, e a resposta inteira ~3,5 s. Com a sessão
aberta (entrada e saída em stream-json), cada pergunta sai em ~1,3 s, e a própria sessão
lembra a conversa ("e da Nova Zelândia?" funciona sem mandar histórico).

As instruções e as permissões são as do jarvis.py (INSTRUCOES, ferramentas liberadas). O que
muda a cada pedido (tarefas, GitLab, agenda, memória) vai junto com a pergunta.

A sessão é renovada depois de OCIOSA_S parada ou MAX_TURNOS perguntas: conversa velha não
ajuda e deixa cada resposta mais cara. Uma pergunta por vez; quem chega espera a vez.
Fora do ar, o jarvis.py volta a chamar o `claude -p` direto, como antes.
"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time

BIN = os.path.dirname(os.path.abspath(__file__))
SOCKET = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis", "cerebro.sock")
OCIOSA_S = 600
MAX_TURNOS = 20
LIMITE_S = 90


def log(msg):
    print(f"{time.strftime('%T')} {msg}", flush=True)


class Sessao:
    def __init__(self, cmd, cwd):
        self.cmd, self.cwd = cmd, cwd
        self.proc, self.turnos, self.ultimo = None, 0, 0.0

    def _abrir(self):
        self.fechar()
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                     env={**os.environ, "CLAUDE_VAULT_RECALL": "0"})
        self.turnos = 0
        log(f"sessão nova (pid {self.proc.pid})")

    def fechar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def perguntar(self, texto, ao_escrever=None):
        """A resposta inteira. ao_escrever(pedaço) recebe o texto conforme o Claude escreve:
        quem fala pode começar pela primeira frase (o bom dia longo ganha uns 3 s)."""
        velha = time.time() - self.ultimo > OCIOSA_S or self.turnos >= MAX_TURNOS
        if self.proc is None or self.proc.poll() is not None or velha:
            self._abrir()
        self.ultimo = time.time()
        self.turnos += 1
        self.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": texto}}) + "\n")
        self.proc.stdin.flush()
        resultado = {}

        def ler():
            for linha in self.proc.stdout:
                try:
                    ev = json.loads(linha)
                except ValueError:
                    continue
                if ao_escrever and ev.get("type") == "stream_event":
                    e = ev.get("event") or {}
                    if e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                        try:
                            ao_escrever(e["delta"]["text"])
                        except Exception:
                            pass
                if ev.get("type") == "result":
                    resultado["texto"] = ev.get("result") or ""
                    resultado["erro"] = ev.get("is_error")
                    return

        leitor = threading.Thread(target=ler, daemon=True)
        leitor.start()
        leitor.join(LIMITE_S)
        if leitor.is_alive() or "texto" not in resultado:
            log("sem resposta a tempo: sessão descartada")
            self.proc.kill()
            self.proc = None
            return None
        return resultado["texto"].strip()


def comando_do_claude():
    """O mesmo `claude` que o jarvis.py montaria, só que em modo sessão."""
    spec = importlib.util.spec_from_file_location("jarvis", os.path.join(BIN, "jarvis.py"))
    jarvis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jarvis)
    cmd = jarvis.comando_claude(sessao=True)
    return cmd, jarvis.VAULT


def servir():
    cmd, cwd = comando_do_claude()
    sessao = Sessao(cmd, cwd)
    vez = threading.Lock()
    os.makedirs(os.path.dirname(SOCKET), exist_ok=True)
    try:
        os.remove(SOCKET)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET)
    os.chmod(SOCKET, 0o600)
    srv.listen(4)
    # aquece: a primeira pergunta de uma sessão nova é a mais lenta
    with vez:
        sessao.perguntar("Responda só: pronto.")
    log("esperando perguntas")

    def atender(con):
        with con:
            try:
                pedido = json.loads(con.makefile().readline())

                def repassar(pedaco):  # em fluxo: cada pedaço vai na hora, uma linha por pedaço
                    con.sendall(json.dumps({"pedaco": pedaco}, ensure_ascii=False).encode() + b"\n")

                with vez:
                    t = time.time()
                    texto = sessao.perguntar(pedido["texto"], repassar if pedido.get("fluxo") else None)
                    log(f"pergunta respondida em {time.time() - t:.1f}s")
                resposta = {"ok": texto is not None, "texto": texto or ""}
            except Exception as e:  # um pedido ruim não derruba o serviço
                resposta = {"ok": False, "erro": str(e)}
            try:
                con.sendall(json.dumps(resposta, ensure_ascii=False).encode() + b"\n")
            except OSError:
                pass

    while True:
        con, _ = srv.accept()
        threading.Thread(target=atender, args=(con,), daemon=True).start()


def perguntar(texto, limite=LIMITE_S + 5, ao_escrever=None):
    """Cliente (jarvis.py): a resposta, ou None se o cérebro não está de pé.
    ao_escrever(pedaço): recebe o texto conforme o Claude escreve."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(limite)
            c.connect(SOCKET)
            c.sendall(json.dumps({"texto": texto, "fluxo": bool(ao_escrever)}, ensure_ascii=False).encode() + b"\n")
            for linha in c.makefile():
                r = json.loads(linha)
                if "pedaco" in r:
                    if ao_escrever:
                        ao_escrever(r["pedaco"])
                    continue
                return r.get("texto") if r.get("ok") else None
    except (OSError, ValueError):
        return None
    return None


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(perguntar(" ".join(sys.argv[1:])))
    else:
        servir()
