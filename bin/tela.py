#!/usr/bin/env python3
"""A tela do Jarvis: um servidor local com a página (tela/index.html) e os dados de agora.

    tela.py          serviço (systemd --user jarvis-tela), em http://127.0.0.1:8765
    tela.py abrir    abre a tela numa janela do Chrome, em tela cheia

Só escuta em 127.0.0.1: nada de fora da máquina chega aqui. A página pergunta /dados a cada
meio segundo; o servidor monta a resposta com o que já existe:
- estado.json: parado, ouvindo, pensando, falando — e a frase que está sendo falada
- briefing.json: clima, agenda, GitLab, tarefas, notícias, melhorias (o bom dia grava;
  se estiver velho, este servidor pede um novo, no máximo a cada 20 min)
- o que está tocando (acao.py agora) e os lembretes (lembrar.py listar), com cache curto
"""
import datetime as dt
import http.server
import json
import os
import subprocess
import sys
import threading
import time

BIN = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(BIN)
PAGINA = os.path.join(REPO, "tela", "index.html")
JV = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis")
ESTADO = os.path.join(JV, "estado.json")
CARTOES = os.path.join(JV, "briefing.json")
AGENDA = os.path.expanduser("~/.claude/cache/agenda.json")
PORTA = int(os.environ.get("JARVIS_TELA_PORTA", "8765"))
ENDERECO = f"http://127.0.0.1:{PORTA}/"
BRIEFING_VELHO_S = 1200


def ler_json(caminho, padrao=None):
    try:
        return json.load(open(caminho))
    except (OSError, ValueError):
        return padrao


class Cache:
    """O resultado de um comando, refeito no máximo a cada `validade` segundos, sem travar a página."""

    def __init__(self, argv, validade):
        self.argv, self.validade, self.valor, self.quando, self.rodando = argv, validade, "", 0.0, False

    def get(self):
        if time.time() - self.quando > self.validade and not self.rodando:
            self.rodando = True
            threading.Thread(target=self._atualizar, daemon=True).start()
        return self.valor

    def _atualizar(self):
        try:
            self.valor = subprocess.run(self.argv, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.quando, self.rodando = time.time(), False


tocando = Cache(["python3", os.path.join(BIN, "acao.py"), "agora"], 4)
lembretes = Cache(["python3", os.path.join(BIN, "lembrar.py"), "listar"], 30)
escuta = Cache(["systemctl", "--user", "is-active", "jarvis-escuta"], 5)
_briefing = {"pedido": 0.0}


def cartoes():
    c = ler_json(CARTOES, {})
    velho = time.time() - (c.get("gerado_em") or 0) > BRIEFING_VELHO_S
    if velho and time.time() - _briefing["pedido"] > BRIEFING_VELHO_S:
        _briefing["pedido"] = time.time()
        subprocess.Popen(["python3", os.path.join(BIN, "briefing.py")], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return c


def reunioes():
    """As reuniões de hoje daqui para frente, com a próxima primeiro."""
    agora = dt.datetime.now().astimezone()
    evs = (ler_json(AGENDA, {}) or {}).get("eventos") or []
    saida = []
    for e in sorted(evs, key=lambda e: e["inicio"]):
        if e["dia_inteiro"] or e["inicio"][:10] != agora.date().isoformat():
            continue
        if dt.datetime.fromisoformat(e["fim"]) > agora:
            saida.append({"hora": e["inicio"][11:16], "titulo": e["titulo"],
                          "agora": dt.datetime.fromisoformat(e["inicio"]) <= agora})
    return saida


def dados():
    e = ler_json(ESTADO, {}) or {}
    if e.get("validade") and time.time() - e.get("desde", 0) > e["validade"]:
        e = {"estado": "parado", "detalhe": ""}
    c = cartoes()
    return {
        "estado": e.get("estado", "parado"), "detalhe": e.get("detalhe", ""),
        "escuta": escuta.get() == "active",
        "clima": c.get("clima"), "noticias": c.get("noticias") or [], "melhorias": c.get("melhorias"),
        "mrs": c.get("mrs") or [], "reviews": c.get("reviews") or [], "tarefas": c.get("tarefas") or [],
        "reunioes": reunioes(), "tocando": tocando.get(),
        "lembretes": [l for l in lembretes.get().splitlines() if l and not l.startswith("nenhum")],
    }


class Pedido(http.server.BaseHTTPRequestHandler):
    def _responder(self, corpo, tipo):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path.startswith("/dados"):
            self._responder(json.dumps(dados(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
        elif self.path in ("/", "/index.html"):
            self._responder(open(PAGINA, "rb").read(), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def log_message(self, *_):
        pass  # meio segundo por pedido: o log seria só ruído


def abrir():
    subprocess.Popen(["google-chrome", f"--app={ENDERECO}", "--start-fullscreen"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["abrir"]:
        abrir()
    else:
        http.server.ThreadingHTTPServer(("127.0.0.1", PORTA), Pedido).serve_forever()
