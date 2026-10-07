#!/usr/bin/env python3
"""Ponte entre o Jarvis e a extensão "Jarvis Música" do Chrome (native messaging).

    musica.py estado | pausar | continuar | proxima | anterior | inicio
    musica.py recarregar      a extensão relê os próprios arquivos (depois de editar)
    musica.py pagina          texto da aba da frente no Chrome (ou só o trecho selecionado)
    musica.py tocar https://music.youtube.com/watch?v=...

Quem abre este arquivo como ponte é o próprio Chrome, quando a extensão conecta (ele passa
"chrome-extension://<id>/" como argumento). Aí ele faz duas coisas ao mesmo tempo:
- conversa com a extensão por stdin/stdout, no formato do native messaging
  (4 bytes de tamanho + JSON);
- abre o socket $XDG_RUNTIME_DIR/jarvis/musica.sock (só seu, 0600), onde o acao.py pede.

Chrome fechado = ponte fechada = socket some, e o acao.py volta ao jeito antigo (abre o app
do YouTube Music por endereço, em janela nova).

Registro da ponte: ~/.config/google-chrome/NativeMessagingHosts/com.jarvis.musica.json.
A extensão mora em ~/.local/share/jarvis/extensao.
"""
import json
import os
import socket
import struct
import sys
import threading

EST = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis")
SOCKET = os.path.join(EST, "musica.sock")
LIMITE_S = 10


# ---------------------------------------------------------------- cliente (acao.py)

def pedir(pedido, limite=LIMITE_S + 2):
    """Manda um pedido à extensão. None se a ponte não está de pé (Chrome fechado, sem extensão)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(limite)
            c.connect(SOCKET)
            c.sendall(json.dumps(pedido).encode() + b"\n")
            return json.loads(c.makefile().readline())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- ponte (o Chrome abre)

class Ponte:
    def __init__(self):
        self.saida = sys.stdout.buffer
        self.trava = threading.Lock()
        self.proximo, self.esperando, self.respostas = 0, {}, {}

    def para_extensao(self, msg):
        dados = json.dumps(msg).encode()
        with self.trava:
            self.saida.write(struct.pack("=I", len(dados)) + dados)
            self.saida.flush()

    def ler_extensao(self):
        entrada = sys.stdin.buffer
        while True:
            tamanho = entrada.read(4)
            if len(tamanho) < 4:
                return  # o Chrome fechou
            msg = json.loads(entrada.read(struct.unpack("=I", tamanho)[0]))
            ev = self.esperando.get(msg.get("id"))
            if ev:
                self.respostas[msg["id"]] = msg
                ev.set()

    def atender(self, con):
        with con:
            try:
                pedido = json.loads(con.makefile().readline())
                with self.trava:
                    self.proximo += 1
                    i = self.proximo
                ev = self.esperando[i] = threading.Event()
                self.para_extensao({**pedido, "id": i})
                resposta = self.respostas.pop(i, None) if ev.wait(LIMITE_S) else None
                self.esperando.pop(i, None)
                resposta = resposta or {"ok": False, "erro": "a extensão não respondeu"}
            except (OSError, ValueError) as e:
                resposta = {"ok": False, "erro": str(e)}
            try:
                con.sendall(json.dumps(resposta).encode() + b"\n")
            except OSError:
                pass

    def rodar(self):
        os.makedirs(EST, exist_ok=True)
        try:
            os.remove(SOCKET)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCKET)
        os.chmod(SOCKET, 0o600)
        srv.listen(4)

        def aceitar():
            while True:
                con, _ = srv.accept()
                threading.Thread(target=self.atender, args=(con,), daemon=True).start()

        threading.Thread(target=aceitar, daemon=True).start()
        try:
            self.ler_extensao()
        finally:
            try:
                os.remove(SOCKET)
            except OSError:
                pass


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0].startswith("chrome-extension://"):
        Ponte().rodar()
    elif args and args[0] in ("estado", "pausar", "continuar", "proxima", "anterior", "inicio", "tocar", "recarregar", "pagina"):
        print(pedir({"acao": args[0], "url": args[1] if len(args) > 1 else ""}))
    else:
        sys.exit(__doc__)
