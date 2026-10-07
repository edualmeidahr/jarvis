#!/usr/bin/env python3
"""Busca as reuniões da semana no Google Calendar e grava em ~/.claude/cache/agenda.json.

    agenda.py                         coleta (o timer das 7h chama assim)
    agenda.py --configurar CLIENTE    login único com o Google, a partir do JSON
                                      do cliente OAuth baixado do Google Cloud

Por que API e não iCal: o endereço secreto do iCal está desligado no Workspace
da empresa, e o endereço público exigiria deixar a agenda aberta na internet.
A API ainda expande as recorrentes sozinha (`singleEvents=true`) — a daily chega
como cinco reuniões com horário certo, sem precisar interpretar RRULE.

Escopo pedido: `calendar.events.readonly` — só lê eventos. Nada é criado,
alterado ou apagado na sua agenda.

Credenciais no chaveiro GNOME, nunca em arquivo:
    service sydle-agenda account cliente   → client_id e client_secret
    service sydle-agenda account refresh   → refresh token do seu login

Saída: 0 se deu certo ou se falta configurar (tentar de novo não ajuda);
       1 se a rede ou a API falharam (o systemd tenta de novo).
"""
import base64
import datetime as dt
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

SECRET_TOOL = os.path.expanduser("~/.local/bin/secret-tool")
CACHE = os.path.expanduser("~/.claude/cache/agenda.json")
ESCOPO = "https://www.googleapis.com/auth/calendar.events.readonly"
FUSO = "America/Sao_Paulo"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTOS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

# eventos que o Google cria sozinho e não são reunião
IGNORAR_TIPOS = {"workingLocation", "birthday"}


# ---------------------------------------------------------------- chaveiro

def chaveiro_ler(conta):
    try:
        r = subprocess.run([SECRET_TOOL, "lookup", "service", "sydle-agenda", "account", conta],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip()


def chaveiro_gravar(conta, valor):
    # com stdin que não é terminal, o secret-tool lê o segredo do stdin
    subprocess.run([SECRET_TOOL, "store", f"--label=Agenda Google ({conta})",
                    "service", "sydle-agenda", "account", conta],
                   input=valor, text=True, check=True, timeout=10)


# ---------------------------------------------------------------- OAuth

def post_form(url, campos):
    corpo = urllib.parse.urlencode(campos).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=corpo), timeout=20) as r:
        return json.load(r)


def configurar(caminho_cliente):
    """Login único: abre o navegador, recebe o código numa porta local, guarda o refresh token.

    Fluxo de app instalado do Google (redirect para 127.0.0.1) com PKCE: o código
    só pode ser trocado por quem gerou o desafio, mesmo que alguém o intercepte.
    """
    with open(caminho_cliente, encoding="utf-8") as f:
        bruto = json.load(f)
    cli = bruto.get("installed") or bruto.get("web") or {}
    if not cli.get("client_id"):
        sys.exit("Esse JSON não parece um cliente OAuth. Baixe o de tipo 'App para computador'.")
    chaveiro_gravar("cliente", json.dumps({"client_id": cli["client_id"],
                                           "client_secret": cli.get("client_secret", "")}))

    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    estado = secrets.token_urlsafe(16)
    recebido = {}

    class Receptor(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404)  # favicon e afins: não é a resposta do Google
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

    servidor = http.server.HTTPServer(("127.0.0.1", 0), Receptor)
    redirect = f"http://127.0.0.1:{servidor.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cli["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": ESCOPO, "access_type": "offline", "prompt": "consent",
        "state": estado, "code_challenge": desafio, "code_challenge_method": "S256",
    })
    print("Abrindo o navegador para você entrar com a conta da SYDLE.")
    print(f"Se não abrir, cole este endereço no navegador:\n\n{url}\n")
    subprocess.run(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # atende até chegar a resposta do Google; o navegador pode pedir outras coisas antes
    servidor.timeout = 1
    prazo = dt.datetime.now() + dt.timedelta(minutes=5)
    while not recebido and dt.datetime.now() < prazo:
        servidor.handle_request()
    servidor.server_close()

    if recebido.get("error"):
        sys.exit(f"O Google recusou: {recebido['error']}. "
                 "Se falar em aprovação do administrador, a empresa bloqueou o app.")
    if not recebido:
        sys.exit("Não recebi a resposta do Google em 5 minutos. Rode de novo.")
    if recebido.get("state") != estado:
        sys.exit("A resposta não bate com o pedido que eu fiz (state diferente). Não troquei o código. Rode de novo.")

    tokens = post_form(TOKEN_URL, {
        "client_id": cli["client_id"], "client_secret": cli.get("client_secret", ""),
        "code": recebido["code"], "code_verifier": verificador,
        "grant_type": "authorization_code", "redirect_uri": redirect,
    })
    if not tokens.get("refresh_token"):
        sys.exit("O Google não devolveu refresh token. Revogue o acesso em myaccount.google.com e rode de novo.")
    chaveiro_gravar("refresh", tokens["refresh_token"])
    print("Login guardado no chaveiro. Pode apagar o JSON do cliente que você baixou.")
    return 0


def token_de_acesso():
    cli_bruto, refresh = chaveiro_ler("cliente"), chaveiro_ler("refresh")
    if not cli_bruto or not refresh:
        return None
    cli = json.loads(cli_bruto)
    return post_form(TOKEN_URL, {"client_id": cli["client_id"], "client_secret": cli["client_secret"],
                                 "refresh_token": refresh, "grant_type": "refresh_token"})["access_token"]


# ---------------------------------------------------------------- semana

def semana_de(hoje):
    """Segunda a sexta da semana útil corrente. No fim de semana, a próxima."""
    if hoje.weekday() >= 5:
        hoje += dt.timedelta(days=7 - hoje.weekday())
    segunda = hoje - dt.timedelta(days=hoje.weekday())
    return segunda, segunda + dt.timedelta(days=4)


def recusei(ev):
    return any(a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees") or [])


def link_chamada(ev):
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for p in (ev.get("conferenceData") or {}).get("entryPoints") or []:
        if p.get("entryPointType") == "video":
            return p.get("uri")
    return None


def normalizar(ev):
    """Evento do Google → o que as telas precisam. None se não é reunião sua."""
    if ev.get("status") == "cancelled" or ev.get("eventType") in IGNORAR_TIPOS or recusei(ev):
        return None
    ini, fim = ev.get("start") or {}, ev.get("end") or {}
    dia_inteiro = "date" in ini
    if dia_inteiro:
        inicio = dt.date.fromisoformat(ini["date"])
        # no Google o fim de evento de dia inteiro é exclusivo: 28 a 29 = só o dia 28
        termino = dt.date.fromisoformat(fim["date"]) - dt.timedelta(days=1)
    else:
        inicio = dt.datetime.fromisoformat(ini["dateTime"])
        termino = dt.datetime.fromisoformat(fim["dateTime"])
    return {
        "id": ev["id"],
        "titulo": (ev.get("summary") or "(sem título)").strip(),
        "dia_inteiro": dia_inteiro,
        "inicio": inicio.isoformat(),
        "fim": termino.isoformat(),
        "link": link_chamada(ev),
        "agenda": ev.get("htmlLink"),
        "tipo_google": ev.get("eventType", "default"),
    }


def buscar(token, de, ate):
    fuso = ZoneInfo(FUSO)  # sem offset fixo: se o horário de verão voltar, continua certo
    inicio = dt.datetime.combine(de, dt.time(), fuso).isoformat()
    fim = dt.datetime.combine(ate + dt.timedelta(days=1), dt.time(), fuso).isoformat()
    params = {"timeMin": inicio, "timeMax": fim,
              "singleEvents": "true", "orderBy": "startTime", "timeZone": FUSO, "maxResults": "250"}
    eventos, pagina = [], None
    while True:
        if pagina:
            params["pageToken"] = pagina
        req = urllib.request.Request(EVENTOS_URL + "?" + urllib.parse.urlencode(params),
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            corpo = json.load(r)
        eventos += corpo.get("items") or []
        pagina = corpo.get("nextPageToken")
        if not pagina:
            return eventos


# ---------------------------------------------------------------- saída

def gravar(dados):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE)


def anterior():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--configurar":
        return configurar(sys.argv[2])

    agora = dt.datetime.now().isoformat(timespec="seconds")
    de, ate = semana_de(dt.date.today())
    try:
        token = token_de_acesso()
        if token is None:
            gravar({**anterior(), "erro": "agenda não configurada — rode agenda.py --configurar", "tentado_em": agora})
            print("agenda: não configurada", file=sys.stderr)
            return 0
        brutos = buscar(token, de, ate)
    except urllib.error.HTTPError as e:
        # 400/401 no refresh = login revogado ou expirado; tentar de novo não resolve
        corpo = e.read().decode(errors="ignore")
        if e.code in (400, 401) and "invalid_grant" in corpo:
            motivo, rc = "login do Google expirou — rode agenda.py --configurar de novo", 0
        elif e.code == 403:
            motivo, rc = "o Google recusou (403) — a API pode estar bloqueada pela empresa", 0
        else:
            motivo, rc = f"API respondeu {e.code}", 1
        gravar({**anterior(), "erro": motivo, "tentado_em": agora})
        print(f"agenda: {motivo}", file=sys.stderr)
        return rc
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        gravar({**anterior(), "erro": f"sem rede: {e}", "tentado_em": agora})
        print(f"agenda: sem rede ({e})", file=sys.stderr)
        return 1

    eventos = [n for n in map(normalizar, brutos) if n]
    gravar({"gerado_em": agora, "erro": None, "semana": [de.isoformat(), ate.isoformat()], "eventos": eventos})
    print(f"agenda: {len(eventos)} evento(s) de {de:%d/%m} a {ate:%d/%m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
