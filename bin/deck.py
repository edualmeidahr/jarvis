#!/usr/bin/env python3
"""O deck do Jarvis: uma página de botões para o celular, tipo Stream Deck, pela rede de casa.

    deck.py            serviço (systemd --user jarvis-deck), na porta 8766
    deck.py endereco   o endereço para abrir no celular, já com a chave
    deck.py qr         abre no Chrome um QR code com esse endereço, para ler com a câmera
    deck.py nova-chave troca a chave: o celular precisa ler o QR de novo
    deck.py microfone  liga e desliga o mudo do microfone (o botão do deck chama isto)

Os botões ficam em ~/.config/jarvis/deck.json (link para config/deck.json). Cada um roda um
comando daquela lista, e só ela: a página manda o número do botão, nunca o comando. Mudou o
arquivo? A página se refaz sozinha em poucos segundos, sem reiniciar o serviço. Fora da lista, só
a barra de volume: um número de 0 a 100, e o alto-falante dela, que liga e desliga o mudo.

Botão com "estado" ganha um ponto que acende: escuta (o "Ei Jarvis" ligado), microfone-mudo,
som-mudo, reuniao-agora (tem reunião acontecendo), modo-reuniao e modo-foco (bin/modo.py). Com
"destaque", a borda acende pelo mesmo nome. Botão com "tipo": "pedir" não roda comando: abre na
página a conversa com o Jarvis.

Pedir ao Jarvis: o texto vai para o jarvis.py como se fosse falado depois de "Ei Jarvis", um
pedido por vez. A resposta é a troca que ele grava em conversa.json (a mesma memória dos
"e depois?"), e ele também fala no computador, como sempre.

Diferente da tela.py, este escuta na rede, porque o celular está fora da máquina. Duas travas:
- a chave: o celular abre /?chave=... uma vez e ganha um cookie; sem ele, tudo responde 403.
  A chave fica em ~/.config/jarvis/deck-chave (0600). É HTTP puro: protege de quem está na
  mesma rede, não de quem consegue ler o tráfego dela;
- só atende endereço das redes locais da máquina (Wi-Fi, cabo). A VPN (tun*) fica de fora:
  ninguém da rede da empresa chega aqui, mesmo com a chave.
"""
import hmac
import http.cookies
import http.server
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse

BIN = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(BIN)
PAGINA = os.path.join(REPO, "tela", "deck.html")
BOTOES = os.path.expanduser("~/.config/jarvis/deck.json")
CHAVE = os.path.expanduser("~/.config/jarvis/deck-chave")
PORTA = int(os.environ.get("JARVIS_DECK_PORTA", "8766"))
ESPERA_S = 8  # depois disso o botão responde "rodando" e o comando segue sozinho
PEDIDO_S = 120  # o Claude buscando na web pode levar um minuto
PEDIDO_MAX = 500
JV = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis")
CONVERSA = os.path.join(JV, "conversa.json")
COOKIE = "deck"

_spec = importlib.util.spec_from_file_location("tela", os.path.join(BIN, "tela.py"))
tela = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tela)

_spec = importlib.util.spec_from_file_location("modo", os.path.join(BIN, "modo.py"))
modo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modo)

tocando = tela.Cache(["python3", os.path.join(BIN, "acao.py"), "agora"], 3)
pedindo = threading.Lock()
escuta = tela.Cache(["systemctl", "--user", "is-active", "jarvis-escuta"], 2)


def chave(nova=False):
    if not nova:
        try:
            return open(CHAVE).read().strip()
        except OSError:
            pass
    c = secrets.token_urlsafe(12)
    os.makedirs(os.path.dirname(CHAVE), exist_ok=True)
    fd = os.open(CHAVE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(c + "\n")
    return c


def redes():
    """[(interface, rede)] das redes locais: tudo com IPv4 global, menos VPN e loopback."""
    try:
        saida = json.loads(subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True).stdout)
    except ValueError:
        return []
    achadas = []
    for i in saida:
        if i["ifname"] == "lo" or re.match(r"(tun|tap|wg|ppp)", i["ifname"]):
            continue
        for a in i.get("addr_info", []):
            if a.get("scope") == "global":
                achadas.append((i["ifname"], ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}")))
    return achadas


_redes = {"valor": [], "quando": 0.0}


def da_rede_local(ip):
    if ip == "127.0.0.1":
        return True
    if time.time() - _redes["quando"] > 30:  # o Wi-Fi troca de rede sem o serviço reiniciar
        _redes["valor"], _redes["quando"] = redes(), time.time()
    ip = ipaddress.ip_address(ip)
    return any(ip in r.network for _, r in _redes["valor"])


def endereco():
    rs = redes()
    if not rs:
        return None
    ip = next((r for nome, r in rs if nome.startswith("wl")), rs[0][1]).ip
    return f"http://{ip}:{PORTA}/?chave={chave()}"


def ler_botoes():
    """As seções com um id em cada botão (a ordem no arquivo) e a versão, para a página saber
    quando se refazer. O comando não sai daqui: a página só vê rótulo e ícone."""
    try:
        secoes = json.load(open(BOTOES, encoding="utf-8"))
        versao = os.stat(BOTOES).st_mtime
    except (OSError, ValueError) as erro:
        return {"versao": 0, "secoes": [], "erro": f"deck.json: {erro}"}, []
    comandos, publico = [], []
    for s in secoes:
        itens = []
        for b in s.get("botoes", []):
            itens.append({"id": len(comandos), "rotulo": b["rotulo"], "icone": b.get("icone", ""),
                          "estado": b.get("estado", ""), "destaque": b.get("destaque", ""),
                          "tipo": b.get("tipo", "")})
            comandos.append([os.path.expanduser(p) for p in b["comando"]] if b.get("comando") else None)
        publico.append({"secao": s.get("secao", ""), "botoes": itens})
    return {"versao": versao, "secoes": publico}, comandos


def apertar(n):
    _, comandos = ler_botoes()
    if not 0 <= n < len(comandos) or not comandos[n]:
        return {"ok": False, "saida": "Esse botão não existe mais. A página vai se refazer."}
    env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + ":/usr/local/bin:/usr/bin:/bin")
    try:
        p = subprocess.Popen(comandos[n], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env=env, start_new_session=True)
    except OSError as erro:
        return {"ok": False, "saida": str(erro)}
    try:
        saida, _ = p.communicate(timeout=ESPERA_S)
    except subprocess.TimeoutExpired:
        threading.Thread(target=p.communicate, daemon=True).start()  # recolhe quando terminar
        return {"ok": True, "saida": "Rodando…"}
    linhas = [l for l in saida.strip().splitlines() if l.strip()]
    return {"ok": p.returncode == 0, "saida": linhas[-1] if linhas else ("Feito." if p.returncode == 0 else
                                                                          f"Saiu com {p.returncode}.")}


SAIDA, ENTRADA = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"


def nivel(no):
    """(volume em %, mudo) de uma saída ou entrada do PipeWire; (None, False) se não deu."""
    try:
        r = subprocess.run(["wpctl", "get-volume", no], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    m = re.search(r"([\d.]+)", r)
    return (round(float(m[1]) * 100) if m else None), "MUTED" in r


def volume(n):
    """A barra do deck mexe no volume do computador, sempre. O "volume" do acao.py, com o Spotify
    tocando, mexe no do Spotify: a barra mostraria um número e o som seria outro."""
    n = max(0, min(100, n))
    subprocess.run(["wpctl", "set-mute", SAIDA, "0"], check=False)
    subprocess.run(["wpctl", "set-volume", SAIDA, f"{n}%"], check=False)
    return {"ok": True, "saida": f"Volume em {n}%."}


def mudo():
    subprocess.run(["wpctl", "set-mute", SAIDA, "toggle"], check=False)
    return {"ok": True, "saida": "Som desligado." if nivel(SAIDA)[1] else "Som de volta."}


def microfone():
    """O mudo vale para a entrada padrão, que é a que o Meet usa. O microfone sem eco do Jarvis lê
    dela também, então o "Ei Jarvis" fica surdo junto, e é o esperado de um mudo."""
    subprocess.run(["wpctl", "set-mute", ENTRADA, "toggle"], check=False)
    return "Microfone mudo." if nivel(ENTRADA)[1] else "Microfone ligado."


def conversa():
    try:
        trocas = json.load(open(CONVERSA))
    except (OSError, ValueError):
        return []
    return [{"quando": t["quando"], "voce": t["voce"], "jarvis": t["jarvis"]} for t in trocas[-8:]]


def pedir(texto):
    """Um pedido por vez: dois jarvis.py juntos falariam um por cima do outro."""
    texto = " ".join(texto.split())[:PEDIDO_MAX]
    if not texto:
        return {"ok": False, "resposta": "Pedido vazio."}
    if not pedindo.acquire(blocking=False):
        return {"ok": False, "resposta": "Ainda estou no pedido anterior."}
    try:
        desde = time.time()
        try:
            r = subprocess.run(["python3", os.path.join(BIN, "jarvis.py"), f"Jarvis, {texto}"],
                               capture_output=True, text=True, timeout=PEDIDO_S)
        except subprocess.TimeoutExpired:
            return {"ok": False, "resposta": "Demorou demais. Se ele terminar, a resposta aparece na conversa."}
        novas = [t for t in conversa() if t["quando"] >= desde]
        if novas:
            return {"ok": True, "resposta": novas[-1]["jarvis"]}
        # música, volume e afins não entram na conversa: vale o que ele imprimiu, se imprimiu
        linhas = [l for l in r.stdout.strip().splitlines() if l.strip()]
        return {"ok": r.returncode == 0, "resposta": linhas[-1] if linhas else "Feito."}
    finally:
        pedindo.release()


def jarvis_agora():
    """parado, ouvindo, pensando ou falando (o estado.json da escuta e da fala)."""
    e = tela.ler_json(tela.ESTADO, {}) or {}
    if e.get("validade") and time.time() - e.get("desde", 0) > e["validade"]:
        return "parado"
    return e.get("estado", "parado")


def estado():
    vol, som_mudo = nivel(SAIDA)
    reunioes = tela.reunioes()
    m = modo.estado()
    return {"tocando": tocando.get(), "volume": vol, "mudo": som_mudo, "reuniao": reunioes[0] if reunioes else None,
            "modo": m, "jarvis": jarvis_agora(), "pedindo": pedindo.locked(),
            "estados": {"escuta": escuta.get() == "active", "microfone-mudo": nivel(ENTRADA)[1],
                        "som-mudo": som_mudo, "reuniao-agora": bool(reunioes and reunioes[0]["agora"]),
                        "modo-reuniao": m["modo"] == "reuniao", "modo-foco": m["modo"] == "foco"}}


PAGINA_QR = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Deck: QR</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<style>body{margin:0;height:100vh;display:grid;place-items:center;background:#050b16;color:#e6f1ff;
font:16px system-ui,sans-serif;text-align:center}#qr{background:#fff;padding:18px;border-radius:12px;
display:inline-block}code{color:#9fb6d1;font-size:13px}</style></head><body><div>
<p>Leia com a câmera do celular (mesmo Wi-Fi)</p><div id="qr"></div><p><code>__URL__</code></p>
<p id="dica" style="color:#5f7795;font-size:13px">Esta janela fecha sozinha quando o celular ler o código.</p>
</div><script>new QRCode(document.getElementById("qr"),{text:"__URL__",width:280,height:280});
// fecha quando o celular entra (o servidor conta as entradas pela chave) ou em 3 minutos: aberta com
// --app, a janela ficava esquecida na dock
const desde = Date.now() / 1000, fechar = () => { document.body.innerHTML = "<p>Pronto, pode fechar.</p>"; window.close(); };
setInterval(async () => {
  try { if ((await (await fetch("/qr/lido")).json()).quando > desde) fechar(); } catch {}
}, 1500);
setTimeout(fechar, 180000);</script>
</body></html>"""


_entrou = {"quando": 0.0}  # a última vez que um celular entrou pela chave (a janela do QR espera por isso)


class Pedido(http.server.BaseHTTPRequestHandler):
    def _responder(self, corpo, tipo, status=200, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, dado):
        self._responder(json.dumps(dado, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _autorizado(self):
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return COOKIE in c and hmac.compare_digest(c[COOKIE].value, chave())

    def _barrar(self):
        """Fora da rede local: some. Sem chave: 403 com a explicação."""
        if not da_rede_local(self.client_address[0]):
            self.close_connection = True
            return True
        url = urllib.parse.urlsplit(self.path)
        pedida = urllib.parse.parse_qs(url.query).get("chave", [""])[0]
        if pedida and hmac.compare_digest(pedida, chave()):
            # a página sai já aqui, sem redirecionar: o QR abre o link a partir de outro app, e
            # cookie novo não ia junto no redirecionamento (testado: dava 403). A página tira a
            # chave da barra de endereço sozinha. Lax, não Strict, pelo mesmo motivo: abrir o deck
            # por um atalho ou pelo histórico também conta como vir "de fora".
            biscoito = f"{COOKIE}={chave()}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Lax"
            _entrou["quando"] = time.time()
            self._responder(open(PAGINA, "rb").read(), "text/html; charset=utf-8", 200, [("Set-Cookie", biscoito)])
            return True
        if self._autorizado():
            return False
        self._responder("Sem chave. No computador, rode: deck.py qr".encode(), "text/plain; charset=utf-8", 403)
        return True

    def do_GET(self):
        caminho = urllib.parse.urlsplit(self.path).path
        if caminho == "/qr/lido" and self.client_address[0] == "127.0.0.1":
            return self._json(_entrou)
        if caminho == "/qr" and self.client_address[0] == "127.0.0.1":
            url = endereco() or "sem rede local"
            return self._responder(PAGINA_QR.replace("__URL__", url).encode(), "text/html; charset=utf-8")
        if self._barrar():
            return
        if caminho == "/":
            self._responder(open(PAGINA, "rb").read(), "text/html; charset=utf-8")
        elif caminho == "/botoes":
            self._json(ler_botoes()[0])
        elif caminho == "/estado":
            self._json(estado())
        elif caminho == "/conversa":
            self._json(conversa())
        else:
            self.send_error(404)

    def do_POST(self):
        if self._barrar():
            return
        caminho = urllib.parse.urlsplit(self.path).path
        if m := re.fullmatch(r"/botao/(\d+)", caminho):
            self._json(apertar(int(m[1])))
        elif m := re.fullmatch(r"/volume/(\d{1,3})", caminho):
            self._json(volume(int(m[1])))
        elif caminho == "/mudo":
            self._json(mudo())
        elif caminho == "/pedir":
            try:
                tamanho = int(self.headers.get("Content-Length", 0))
                corpo = json.loads(self.rfile.read(min(tamanho, 4096)) or b"{}")
            except ValueError:
                return self.send_error(400)
            self._json(pedir(str(corpo.get("texto", ""))))
        else:
            self.send_error(404)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    arg = sys.argv[1:]
    if arg == ["endereco"]:
        print(endereco() or "sem rede local (só VPN ou desconectado)")
    elif arg == ["qr"]:
        subprocess.Popen(["google-chrome", f"--app=http://127.0.0.1:{PORTA}/qr"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    elif arg == ["microfone"]:
        print(microfone())
    elif arg == ["nova-chave"]:
        chave(nova=True)
        print("Chave nova. Abra o QR de novo: deck.py qr")
    elif arg:
        sys.exit(__doc__)
    else:
        chave()
        http.server.ThreadingHTTPServer(("0.0.0.0", PORTA), Pedido).serve_forever()
