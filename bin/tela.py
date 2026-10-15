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
import importlib.util
import json
import os
import re
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
NIVEL_VOZ = os.path.join(JV, "voz-nivel.json")  # falar.py: a curva de volume da frase que toca
NIVEL_MIC = os.path.join(JV, "mic-nivel.json")
PALCO = os.path.join(JV, "palco")  # jarvis.py: existe enquanto o bom dia fala  # escuta.py: o volume do microfone enquanto ouve


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


class CacheFn:
    """O resultado de uma função, refeito no máximo a cada `validade` segundos, sem travar a página."""

    def __init__(self, fn, validade):
        self.fn, self.validade, self.valor, self.quando, self.rodando = fn, validade, None, 0.0, False

    def get(self):
        if time.time() - self.quando > self.validade and not self.rodando:
            self.rodando = True
            threading.Thread(target=self._atualizar, daemon=True).start()
        return self.valor

    def _atualizar(self):
        try:
            self.valor = self.fn()
        except Exception:
            self.valor = None
        self.quando, self.rodando = time.time(), False


def _modulo(nome):
    spec = importlib.util.spec_from_file_location(nome, os.path.join(BIN, f"{nome}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


spotify = _modulo("spotify")
_fila = {"quando": 0.0, "proxima": ""}


def musica_spotify():
    """O que toca no Spotify, para o cartão: capa, música, artista, progresso e a próxima da fila.
    None sem Spotify ou sem nada tocando (o cartão cai no texto do acao.py agora)."""
    if not spotify.ligado():
        return None
    r = spotify.api("GET", "/me/player")
    item = r.get("item") or {}
    if not item:
        return None
    imagens = (item.get("album") or {}).get("images") or []
    if time.time() - _fila["quando"] > 20:  # a fila muda pouco: 1 pedido a cada 20 s
        _fila["quando"] = time.time()
        try:
            q = (spotify.api("GET", "/me/player/queue").get("queue") or [{}])[0]
            _fila["proxima"] = f"{q.get('name', '')} — {', '.join(a['name'] for a in q.get('artists', [])[:1])}".strip(" —")
        except spotify.SemSpotify:
            _fila["proxima"] = ""
    return {"titulo": item.get("name", ""), "artista": ", ".join(a["name"] for a in item.get("artists", [])[:2]),
            "capa": (imagens[1] if len(imagens) > 1 else imagens[0])["url"] if imagens else "",
            "progresso_ms": r.get("progress_ms") or 0, "duracao_ms": item.get("duration_ms") or 0,
            "tocando": bool(r.get("is_playing")), "proxima": _fila["proxima"], "quando": time.time(),
            "dispositivo": (r.get("device") or {}).get("name", "")}


def nivel():
    """O volume de agora (0 a 1): a voz do Jarvis pela curva da frase que toca, e o microfone."""
    agora, voz, mic = time.time(), None, None
    v = ler_json(NIVEL_VOZ)
    if v:
        i = int((agora - v["inicio"]) / v["passo"])
        if 0 <= i < len(v["niveis"]):
            voz = v["niveis"][i]
    m = ler_json(NIVEL_MIC)
    if m and agora - m["t"] < 0.4:
        mic = m["nivel"]
    return {"voz": voz, "mic": mic}


musica = CacheFn(musica_spotify, 3)
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


def reunioes_do_dia():
    """Todas as reuniões de hoje, inclusive as que já passaram (a linha do tempo mostra o dia todo)."""
    hoje = dt.date.today().isoformat()
    evs = (ler_json(AGENDA, {}) or {}).get("eventos") or []
    return [{"titulo": e["titulo"], "inicio": e["inicio"], "fim": e["fim"]} for e in sorted(evs, key=lambda e: e["inicio"])
            if not e["dia_inteiro"] and e["inicio"][:10] == hoje]


def reunioes():
    """As reuniões de hoje daqui para frente, com a próxima primeiro."""
    agora = dt.datetime.now().astimezone()
    evs = (ler_json(AGENDA, {}) or {}).get("eventos") or []
    saida = []
    for e in sorted(evs, key=lambda e: e["inicio"]):
        if e["dia_inteiro"] or e["inicio"][:10] != agora.date().isoformat():
            continue
        if dt.datetime.fromisoformat(e["fim"]) > agora:
            saida.append({"hora": e["inicio"][11:16], "titulo": e["titulo"], "inicio": e["inicio"], "fim": e["fim"],
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
        "ontem": c.get("ontem"), "futebol": c.get("futebol") or "",
        # o bom dia está falando: a tela vira apresentação (um assunto grande por vez)
        "palco": os.path.exists(PALCO) and time.time() - os.path.getmtime(PALCO) < 600,
        "mrs": c.get("mrs") or [], "reviews": c.get("reviews") or [], "tarefas": c.get("tarefas") or [],
        "reunioes": reunioes(), "dia": reunioes_do_dia(), "tocando": tocando.get(), "musica": musica.get(),
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
        if self.path.startswith("/nivel"):
            self._responder(json.dumps(nivel()).encode(), "application/json")
        elif self.path.startswith("/dados"):
            self._responder(json.dumps(dados(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
        elif self.path in ("/", "/index.html"):
            self._responder(open(PAGINA, "rb").read(), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def log_message(self, *_):
        pass  # meio segundo por pedido: o log seria só ruído


def monitor_maior():
    """(x, y) do maior monitor ligado: com o notebook e um monitor externo, a tela abre no externo
    (no notebook, 768 px de altura, cabe menos)."""
    try:
        saida = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    monitores = [tuple(map(int, m)) for m in re.findall(r" connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", saida)]
    if not monitores:
        return None
    w, h, x, y = max(monitores, key=lambda m: m[0] * m[1])
    return x, y


def abrir():
    pos = monitor_maior()
    onde = [f"--window-position={pos[0]},{pos[1]}"] if pos else []
    subprocess.Popen(["google-chrome", f"--app={ENDERECO}", *onde, "--start-fullscreen"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["abrir"]:
        abrir()
    else:
        def manter_fresco():
            """Clima (30 min) e notícias (1 h) sempre no cache: no "bom dia" eles não vão à internet."""
            while True:
                subprocess.run(["python3", os.path.join(BIN, "briefing.py")], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=60)
                time.sleep(900)

        threading.Thread(target=manter_fresco, daemon=True).start()
        http.server.ThreadingHTTPServer(("127.0.0.1", PORTA), Pedido).serve_forever()
