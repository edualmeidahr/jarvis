#!/usr/bin/env python3
"""O Spotify do Jarvis: a Web API manda tocar no dispositivo "Jarvis" (o spotifyd, sem janela).

    spotify.py configurar          login único: abre o navegador para autorizar o app
    spotify.py tocar <busca>       playlist sua, artista ou música, no dispositivo Jarvis
    spotify.py agora               o que está tocando (pela API)
    spotify.py dispositivos        os dispositivos que a conta vê
    spotify.py playlists           as suas playlists
    spotify.py sugestoes           artistas que você mais ouve e suas playlists

Peças:
- o player é o spotifyd (serviço jarvis-spotifyd), com login próprio em ~/.cache/spotifyd
- este script fala com a Web API em nome do seu usuário. O app é o seu, do painel de
  desenvolvedor do Spotify; o Client ID fica em JARVIS_SPOTIFY_CLIENT_ID no local.env (não é
  segredo). O login é PKCE (sem Client secret); o refresh token fica no chaveiro e o token de
  acesso, que vale 1 hora, em ~/.cache/jarvis/spotify-token.json
- pausar, próxima, "que música é essa?" e o ducking não passam por aqui: o spotifyd aparece no
  MPRIS e no PipeWire como qualquer player, e o acao.py já cuida disso
"""
import base64
import difflib
import hashlib
import http.server
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

BIN = os.path.dirname(os.path.realpath(__file__))
SECRET_TOOL = os.path.expanduser("~/.local/bin/secret-tool")
TOKEN = os.path.expanduser("~/.cache/jarvis/spotify-token.json")
API = "https://api.spotify.com/v1"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT = "http://127.0.0.1:8899/callback"  # a mesma cadastrada no app do painel
DISPOSITIVO = "Jarvis"  # device_name do config/spotifyd/spotifyd.conf
ESCOPOS = ("user-read-playback-state user-modify-playback-state user-read-currently-playing "
           "playlist-read-private playlist-read-collaborative user-library-read user-top-read "
           "user-read-recently-played")
REDE_S = 8

_spec = importlib.util.spec_from_file_location("config", os.path.join(BIN, "config.py"))
config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config)


class SemSpotify(Exception):
    """O Spotify não dá agora: sem login, sem rede ou sem o dispositivo. Quem chama cai no YouTube Music."""


def client_id():
    return config.get("JARVIS_SPOTIFY_CLIENT_ID")


def ligado():
    """O Jarvis deve tocar pelo Spotify? (JARVIS_MUSICA=spotify, com Client ID e login feitos)"""
    return config.get("JARVIS_MUSICA").lower() == "spotify" and bool(client_id()) and bool(chaveiro_ler())


# ---------------------------------------------------------------- login

def chaveiro_ler():
    try:
        r = subprocess.run([SECRET_TOOL, "lookup", "service", "jarvis-spotify", "account", "refresh"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip()


def chaveiro_gravar(valor):
    # com stdin que não é terminal, o secret-tool lê o segredo do stdin
    subprocess.run([SECRET_TOOL, "store", "--label=Jarvis Spotify (refresh)",
                    "service", "jarvis-spotify", "account", "refresh"],
                   input=valor, text=True, check=True, timeout=10)


def _post_token(campos):
    corpo = urllib.parse.urlencode({"client_id": client_id(), **campos}).encode()
    req = urllib.request.Request(TOKEN_URL, data=corpo,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=REDE_S) as r:
        return json.load(r)


def _guardar(tokens):
    if tokens.get("refresh_token"):  # o Spotify às vezes troca o refresh token: guarda o novo
        chaveiro_gravar(tokens["refresh_token"])
    os.makedirs(os.path.dirname(TOKEN), exist_ok=True)
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"access_token": tokens["access_token"],
                   "vence": time.time() + tokens.get("expires_in", 3600) - 60}, f)
    return tokens["access_token"]


def configurar():
    """Login único com PKCE: abre o navegador, recebe o código em 127.0.0.1:8899, guarda o refresh."""
    if not client_id():
        sys.exit("Falta JARVIS_SPOTIFY_CLIENT_ID no ~/.config/jarvis/local.env.")
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    estado = secrets.token_urlsafe(16)
    recebido = {}

    class Receptor(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404)  # favicon e afins
                self.end_headers()
                return
            recebido.update({k: v[0] for k, v in q.items()})
            ok = "code" in q and q.get("state", [""])[0] == estado
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Pronto. Pode fechar esta aba." if ok else "Não deu certo. Volte ao terminal."
            self.wfile.write(f"<p style='font:18px sans-serif;margin:3em'>{msg}</p>".encode())

        def log_message(self, *a):
            pass

    servidor = http.server.HTTPServer(("127.0.0.1", 8899), Receptor)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id(), "response_type": "code", "redirect_uri": REDIRECT, "scope": ESCOPOS,
        "state": estado, "code_challenge": desafio, "code_challenge_method": "S256",
    })
    print("Abrindo o navegador para você autorizar o app no Spotify.")
    print(f"Se não abrir, cole este endereço no navegador:\n\n{url}\n")
    subprocess.run(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    servidor.timeout = 1
    prazo = time.time() + 300
    while not recebido and time.time() < prazo:
        servidor.handle_request()
    servidor.server_close()
    if recebido.get("error"):
        sys.exit(f"O Spotify recusou: {recebido['error']}.")
    if not recebido:
        sys.exit("Não recebi a resposta do Spotify em 5 minutos. Rode de novo.")
    if recebido.get("state") != estado:
        sys.exit("A resposta não bate com o pedido (state diferente). Não troquei o código. Rode de novo.")
    tokens = _post_token({"grant_type": "authorization_code", "code": recebido["code"],
                          "redirect_uri": REDIRECT, "code_verifier": verificador})
    if not tokens.get("refresh_token"):
        sys.exit("O Spotify não devolveu refresh token. Rode de novo.")
    _guardar(tokens)
    eu = api("GET", "/me")
    print(f"Pronto: autorizado como {eu.get('display_name') or eu.get('id')} ({eu.get('product')}).")
    return 0


def token(renovar=False):
    if not renovar:
        try:
            t = json.load(open(TOKEN))
            if time.time() < t["vence"]:
                return t["access_token"]
        except (OSError, ValueError, KeyError):
            pass
    refresh = chaveiro_ler()
    if not refresh or not client_id():
        raise SemSpotify("sem login (rode spotify.py configurar)")
    try:
        return _guardar(_post_token({"grant_type": "refresh_token", "refresh_token": refresh}))
    except (OSError, ValueError, KeyError) as e:
        raise SemSpotify(f"não renovei o login: {e}")


# ---------------------------------------------------------------- API

def api(metodo, caminho, corpo=None, params=None, _de_novo=True):
    """Chama a Web API. Devolve o JSON (ou {} quando a resposta é vazia). Erro vira SemSpotify."""
    url = API + caminho + ("?" + urllib.parse.urlencode(params) if params else "")
    dados = json.dumps(corpo).encode() if corpo is not None else (b"" if metodo in ("PUT", "POST") else None)
    req = urllib.request.Request(url, data=dados, method=metodo, headers={
        "Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REDE_S) as r:
            bruto = r.read()
            return json.loads(bruto) if bruto.strip() else {}
    except urllib.error.HTTPError as e:
        if e.code == 401 and _de_novo:  # token vencido antes da hora: renova uma vez
            token(renovar=True)
            return api(metodo, caminho, corpo, params, _de_novo=False)
        try:
            msg = json.load(e).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            msg = ""
        raise SemSpotify(f"{e.code} {msg}".strip())
    except (OSError, ValueError) as e:
        raise SemSpotify(str(e))


def dispositivo(espera_s=6.0):
    """O id do dispositivo Jarvis. O spotifyd some da lista se ficou muito tempo parado sem
    conexão: aí reinicia o serviço e espera ele voltar."""
    reiniciou = False
    fim = time.time() + espera_s
    while True:
        for d in api("GET", "/me/player/devices").get("devices", []):
            if d.get("name") == DISPOSITIVO:
                return d["id"]
        if not reiniciou:
            subprocess.run(["systemctl", "--user", "restart", "jarvis-spotifyd"], check=False)
            reiniciou = True
        if time.time() > fim:
            raise SemSpotify("o dispositivo Jarvis não apareceu (serviço jarvis-spotifyd)")
        time.sleep(0.5)


# ---------------------------------------------------------------- busca

def normalizar(texto):
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s.replace("&", " e ")).strip()


def parecido(a, b):
    a, b = normalizar(a), normalizar(b)
    return max(difflib.SequenceMatcher(None, a, b).ratio(),
               difflib.SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio())


def playlists():
    """As suas playlists (e as que você segue): [{nome, uri, dono}]."""
    saida, pagina = [], {"limit": 50, "offset": 0}
    while pagina["offset"] < 300:
        r = api("GET", "/me/playlists", params=pagina)
        saida += [{"nome": p["name"], "uri": p["uri"], "dono": (p.get("owner") or {}).get("display_name", "")}
                  for p in r.get("items") or [] if p]
        if not r.get("next"):
            break
        pagina["offset"] += 50
    return saida


def buscar(busca, tipos):
    # limite 10: é o máximo que apps em modo de desenvolvimento podem pedir na busca
    return api("GET", "/search", params={"q": busca, "type": ",".join(tipos), "limit": 10})


def escolher(busca, playlist=False):
    """O que tocar para a busca: {tipo, nome, artista, uri}. Ordem:
    1. uma playlist SUA com nome parecido (pedido de playlist, ou nome quase igual)
    2. pedido de playlist: a primeira playlist da busca
    3. o nome de um artista (quase igual): o artista
    4. a primeira música da busca"""
    minhas = playlists()
    if minhas:
        p = max(minhas, key=lambda p: parecido(busca, p["nome"]))
        if parecido(busca, p["nome"]) >= (0.7 if playlist else 0.9):
            return {"tipo": "playlist", "nome": p["nome"], "artista": "", "uri": p["uri"], "sua": True}
    if playlist:
        achadas = [p for p in buscar(busca, ["playlist"]).get("playlists", {}).get("items") or [] if p]
        if achadas:
            p = achadas[0]
            return {"tipo": "playlist", "nome": p["name"], "artista": "", "uri": p["uri"], "sua": False}
    r = buscar(busca, ["artist", "track"])
    artistas = [a for a in r.get("artists", {}).get("items") or [] if a]
    if artistas:
        a = max(artistas[:3], key=lambda a: parecido(busca, a["name"]))
        if parecido(busca, a["name"]) >= 0.85:
            return {"tipo": "artista", "nome": a["name"], "artista": a["name"], "uri": a["uri"]}
    faixas = [t for t in r.get("tracks", {}).get("items") or [] if t]
    if faixas:
        t = faixas[0]
        return {"tipo": "musica", "nome": t["name"], "artista": ", ".join(x["name"] for x in t["artists"][:2]),
                "uri": t["uri"]}
    return None


def tocar_achado(achado):
    """Toca o que o escolher() achou no dispositivo Jarvis (música solta ou contexto inteiro)."""
    alvo = dispositivo()
    corpo = {"uris": [achado["uri"]]} if achado["tipo"] == "musica" else {"context_uri": achado["uri"]}
    api("PUT", "/me/player/play", corpo=corpo, params={"device_id": alvo})


def tocar(busca, playlist=False):
    """Escolhe e toca. Devolve o que escolheu (dict) ou None se não achou.
    SemSpotify quando o Spotify não dá agora."""
    achado = escolher(busca, playlist)
    if achado:
        tocar_achado(achado)
    return achado


def agora():
    r = api("GET", "/me/player/currently-playing")
    item = r.get("item") or {}
    if not item:
        return None
    return {"tocando": bool(r.get("is_playing")), "titulo": item.get("name", ""),
            "artista": ", ".join(a["name"] for a in item.get("artists", [])[:2])}


def sugestoes(n=6):
    """Artistas que você mais ouviu nas últimas semanas, para o fim do bom dia."""
    try:
        r = api("GET", "/me/top/artists", params={"limit": n, "time_range": "short_term"})
    except SemSpotify:
        return []
    return [a["name"] for a in r.get("items") or [] if a]


# ---------------------------------------------------------------- cli

def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd, resto = args[0], " ".join(args[1:]).strip()
    try:
        if cmd == "configurar":
            return configurar()
        if cmd == "tocar" and resto:
            playlist = bool(re.search(r"\bplaylists?\b", resto, re.I))
            termo = re.sub(r"\b(a |uma )?playlists?( de| do| da)?\b", "", resto, flags=re.I).strip() or resto
            achado = tocar(termo, playlist)
            print(json.dumps(achado, ensure_ascii=False) if achado else "não achei")
            return 0 if achado else 1
        if cmd == "agora":
            print(json.dumps(agora(), ensure_ascii=False))
            return 0
        if cmd == "dispositivos":
            for d in api("GET", "/me/player/devices").get("devices", []):
                print(f"{d['name']} ({d['type']}){' — ativo' if d.get('is_active') else ''}")
            return 0
        if cmd == "playlists":
            for p in playlists():
                print(f"{p['nome']} — {p['dono']}")
            return 0
        if cmd == "sugestoes":
            print("\n".join(sugestoes()))
            return 0
    except SemSpotify as e:
        print(f"Spotify indisponível: {e}")
        return 2
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
