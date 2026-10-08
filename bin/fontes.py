#!/usr/bin/env python3
"""Fontes rápidas e atuais, para não depender da busca na web (lenta e atrasada para o que é ao vivo).

    fontes.py futebol [time]          placar ao vivo, último resultado ou próximo jogo
    fontes.py noticias "<tema>"       manchetes das últimas 24 h sobre o tema

Futebol: placar público da ESPN (sem chave), nos campeonatos de LIGAS. Sem time, usa o
JARVIS_TIME do local.env. Notícias: RSS de busca do Google Notícias.

Medido em 07/10: a busca na web do Claude levou ~20 s e disse que o último jogo do Cruzeiro
tinha sido em agosto, com o Cruzeiro jogando naquele minuto. Esta fonte respondeu na hora:
"Cruzeiro 1 x 0 São Paulo, segundo tempo, 51 minutos".
"""
import datetime as dt
import gzip
import importlib.util
import json
import os
import re
import sys
import threading
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BIN = os.path.dirname(os.path.realpath(__file__))
_spec = importlib.util.spec_from_file_location("config", os.path.join(BIN, "config.py"))
config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config)

REDE_S = 6
LIGAS = ["bra.1", "bra.2", "bra.copa_do_brazil", "conmebol.libertadores", "conmebol.sudamericana"]
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def normalizar(t):
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower().strip()


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=REDE_S) as r:
        dados = r.read()
    # a ESPN manda gzip nas consultas com intervalo de datas mesmo sem pedir; antes o erro era
    # engolido e o resultado saía "nenhum jogo"
    return gzip.decompress(dados) if dados[:2] == b"\x1f\x8b" else dados


# ---------------------------------------------------------------- futebol

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}"
CACHE_TIMES = os.path.expanduser("~/.claude/cache/espn-times.json")


def _json(url):
    return json.loads(baixar(url))


def _id_do_time(time):
    """(liga, id) do time na ESPN, procurando nas LIGAS; o mapa fica em cache (muda pouco)."""
    try:
        mapa = json.load(open(CACHE_TIMES))
    except (OSError, ValueError):
        mapa = {}
        for liga in LIGAS[:2]:  # Série A e B: onde mora o time; a agenda é por liga
            try:
                for t in _json(BASE.format(liga=liga) + "/teams")["sports"][0]["leagues"][0]["teams"]:
                    t = t["team"]
                    mapa.setdefault(normalizar(t["displayName"]), [liga, t["id"], t["displayName"]])
            except Exception:
                continue
        if mapa:
            os.makedirs(os.path.dirname(CACHE_TIMES), exist_ok=True)
            json.dump(mapa, open(CACHE_TIMES, "w"), ensure_ascii=False)
    alvo = normalizar(time)
    for nome, valor in mapa.items():
        if alvo == nome or alvo in nome:
            return valor
    return None


def _jogo(e):
    c = e["competitions"][0]
    lados = {x["homeAway"]: x for x in c["competitors"]}
    gol = lambda x: (x.get("score") or {}).get("displayValue") if isinstance(x.get("score"), dict) else x.get("score")
    st = (c.get("status") or e.get("status"))["type"]
    return {"casa": lados["home"]["team"]["displayName"], "fora": lados["away"]["team"]["displayName"],
            "gols_casa": gol(lados["home"]), "gols_fora": gol(lados["away"]),
            "estado": st["state"], "detalhe": st.get("description", ""),
            "relogio": (c.get("status") or e.get("status", {})).get("displayClock", ""),
            "inicio": dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone()}


def _ao_vivo(time):
    """O jogo do time em andamento hoje, em qualquer uma das LIGAS (consultas em paralelo)."""
    alvo, achados = normalizar(time), []

    def olhar(liga):
        try:
            for e in _json(BASE.format(liga=liga) + "/scoreboard").get("events") or []:
                j = _jogo(e)
                if j["estado"] == "in" and (alvo in normalizar(j["casa"]) or alvo in normalizar(j["fora"])):
                    achados.append(j)
        except Exception:
            pass

    fios = [threading.Thread(target=olhar, args=(liga,)) for liga in LIGAS]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    return achados[0] if achados else None


def _quando(inicio):
    hoje = dt.date.today()
    dia = inicio.date()
    nome = "hoje" if dia == hoje else "amanhã" if dia == hoje + dt.timedelta(days=1) else \
        "ontem" if dia == hoje - dt.timedelta(days=1) else DIAS[dia.weekday()] if abs((dia - hoje).days) < 7 else f"{dia:%d/%m}"
    return nome, f"{inicio:%H}h{inicio:%M}".replace("h00", "h")


def _placar(j):
    return f"{j['casa']} {j['gols_casa']} x {j['gols_fora']} {j['fora']}"


def futebol(time=None):
    time = time or config.get("JARVIS_TIME", "Cruzeiro")
    j = _ao_vivo(time)
    if j:
        minuto_n = int(re.sub(r"\D", "", j["relogio"].split("+")[0]) or 0)
        tempo = {"First Half": "primeiro tempo", "Second Half": "segundo tempo", "Halftime": "intervalo"}.get(
            j["detalhe"], "segundo tempo" if minuto_n > 45 else "primeiro tempo")
        minuto = f", {j['relogio'].rstrip(chr(39))} minutos" if j["relogio"] and tempo != "intervalo" else ""
        return f"Ao vivo: {_placar(j)}, {tempo}{minuto}."
    achado = _id_do_time(time)
    if not achado:
        return f"Não achei o {time} na Série A nem na B."
    liga, tid, nome = achado
    partes = []
    try:
        feitos = [_jogo(e) for e in _json(BASE.format(liga=liga) + f"/teams/{tid}/schedule").get("events") or []]
        feitos = sorted([x for x in feitos if x["estado"] == "post"], key=lambda x: x["inicio"])
        if feitos:
            dia, _ = _quando(feitos[-1]["inicio"])
            partes.append(f"Último jogo, {dia}: {_placar(feitos[-1])}.")
        futuros = [_jogo(e) for e in _json(BASE.format(liga=liga) + f"/teams/{tid}/schedule?fixture=true").get("events") or []]
        futuros = sorted([x for x in futuros if x["estado"] == "pre"], key=lambda x: x["inicio"])
        if futuros:
            dia, hora = _quando(futuros[0]["inicio"])
            partes.append(f"Próximo: {futuros[0]['casa']} x {futuros[0]['fora']}, {dia} às {hora}.")
    except Exception:
        return f"Não consegui ver os jogos do {nome} agora."
    return " ".join(partes) or f"Não achei jogos do {nome}."


# ---------------------------------------------------------------- notícias

def noticias(tema, n=3):
    url = ("https://news.google.com/rss/search?hl=pt-BR&gl=BR&ceid=BR:pt-419&q="
           + urllib.parse.quote(f"{tema} when:1d"))
    try:
        itens = list(ET.fromstring(baixar(url)).iter("item"))
    except Exception:
        return f"Não consegui buscar notícias sobre {tema} agora."
    vistas, manchetes = set(), []
    for item in itens:
        titulo = (item.findtext("title") or "").rpartition(" - ")[0]
        chave = normalizar(titulo)[:40]
        if titulo and chave not in vistas:
            vistas.add(chave)
            manchetes.append(titulo.rstrip("."))
        if len(manchetes) == n:
            break
    if not manchetes:
        return f"Nenhuma notícia sobre {tema} nas últimas 24 horas."
    return f"Sobre {tema}: " + " ".join(f"{m}." for m in manchetes)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["futebol"]:
        print(futebol(" ".join(args[1:]) or None))
    elif args[:1] == ["noticias"] and len(args) > 1:
        print(noticias(" ".join(args[1:])))
    else:
        sys.exit(__doc__)
