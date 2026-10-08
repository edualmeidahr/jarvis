#!/usr/bin/env python3
"""O bom dia do Jarvis: junta clima, agenda, GitLab, tarefas, notícias e as melhorias dele.

    briefing.py            imprime os dados do bom dia (o que vai para o Claude)
    briefing.py clima      a frase do clima
    briefing.py noticias   a frase das notícias

Fontes, todas sem chave:
- clima: Open-Meteo (cidade em JARVIS_CIDADE no local.env; padrão Belo Horizonte)
- notícias: RSS do Google Notícias, um tema por manchete (JARVIS_NOTICIAS, separado por
  vírgula; "geral" = manchetes do dia; padrão "geral, inteligência artificial, tecnologia")
- melhorias: commits do repositório do Jarvis desde ontem. É a fala do vídeo ("desde ontem
  foram 43 melhorias no meu próprio sistema"), só que com número de verdade
- agenda, GitLab e tarefas: os caches que o manha.sh já mantém

Cada fonte tem cache em ~/.claude/cache e tempo curto de rede: sem internet, o bom dia sai
com o que der. Também grava $XDG_RUNTIME_DIR/jarvis/briefing.json (os cartões da tela futura).
"""
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# realpath: rodando pelo link ~/.claude/bin, o abspath achava ~/.claude como repositório (0 melhorias)
BIN = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(BIN)
CACHE = os.path.expanduser("~/.claude/cache")
CARTOES = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis", "briefing.json")
REDE_S = 6

_spec = importlib.util.spec_from_file_location("config", os.path.join(BIN, "config.py"))
config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config)

# códigos de tempo da OMS que o Open-Meteo usa, em palavras faladas
TEMPO = {0: "céu limpo", 1: "quase sem nuvens", 2: "parcialmente nublado", 3: "nublado", 45: "neblina", 48: "neblina",
         51: "garoa fraca", 53: "garoa", 55: "garoa forte", 61: "chuva fraca", 63: "chuva", 65: "chuva forte",
         66: "chuva congelada", 67: "chuva congelada", 71: "neve", 73: "neve", 75: "neve", 80: "pancadas de chuva",
         81: "pancadas de chuva", 82: "temporal", 95: "tempestade", 96: "tempestade com granizo", 99: "tempestade com granizo"}


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=REDE_S) as r:
        return r.read()


def com_cache(nome, validade_s, buscar):
    caminho = os.path.join(CACHE, f"{nome}.json")
    try:
        if time.time() - os.path.getmtime(caminho) < validade_s:
            return json.load(open(caminho))
    except (OSError, ValueError):
        pass
    try:
        dados = buscar()
    except Exception:  # rede fora: o último dado bom, mesmo velho
        try:
            return json.load(open(caminho))
        except (OSError, ValueError):
            return None
    os.makedirs(CACHE, exist_ok=True)
    json.dump(dados, open(caminho, "w"), ensure_ascii=False)
    return dados


# ---------------------------------------------------------------- clima

def clima():
    cidade = config.get("JARVIS_CIDADE", "Belo Horizonte")

    def buscar():
        geo = json.loads(baixar("https://geocoding-api.open-meteo.com/v1/search?count=1&language=pt&name="
                                + urllib.parse.quote(cidade)))["results"][0]
        d = json.loads(baixar(
            f"https://api.open-meteo.com/v1/forecast?latitude={geo['latitude']}&longitude={geo['longitude']}"
            "&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,weather_code&timezone=America%2FSao_Paulo&forecast_days=2"))
        return {"cidade": geo["name"], "agora": d["current"], "dias": d["daily"]}

    return com_cache("clima", 1800, buscar)


def frase_clima(c=None, quando="hoje"):
    c = c or clima()
    if not c:
        return "Não consegui ver o clima agora."
    i = 0 if quando == "hoje" else 1
    d = c["dias"]
    chuva = d["precipitation_probability_max"][i]
    partes = []
    if quando == "hoje":
        partes.append(f"Agora faz {round(c['agora']['temperature_2m'])} graus em {c['cidade']}, "
                      f"{TEMPO.get(c['agora']['weather_code'], 'tempo indefinido')}.")
    partes.append(f"{'Hoje' if quando == 'hoje' else 'Amanhã'} vai de {round(d['temperature_2m_min'][i])} "
                  f"a {round(d['temperature_2m_max'][i])} graus, com {chuva}% de chance de chuva.")
    return " ".join(partes)


# ---------------------------------------------------------------- notícias

def noticias(n=3):
    temas = [t.strip() for t in config.get("JARVIS_NOTICIAS", "geral, inteligência artificial, tecnologia").split(",")
             if t.strip()]

    def buscar():
        vistas, saida = set(), []
        for tema in temas:
            if tema.lower() == "geral":
                url = "https://news.google.com/rss?hl=pt-BR&gl=BR&ceid=BR:pt-419"
            else:
                url = ("https://news.google.com/rss/search?hl=pt-BR&gl=BR&ceid=BR:pt-419&q="
                       + urllib.parse.quote(f"{tema} when:1d"))
            try:
                itens = ET.fromstring(baixar(url)).iter("item")
            except Exception:
                continue
            for item in itens:
                titulo = item.findtext("title") or ""
                titulo, _, fonte = titulo.rpartition(" - ")  # "Manchete - G1"
                chave = titulo.lower()[:40]
                if titulo and chave not in vistas:
                    vistas.add(chave)
                    saida.append({"tema": tema, "titulo": titulo, "fonte": fonte})
                    break
        if not saida:
            raise OSError("sem notícias")
        return saida

    return (com_cache("noticias", 3600, buscar) or [])[:n]


def frase_noticias(ns=None):
    ns = ns if ns is not None else noticias()
    if not ns:
        return "Não consegui ver as notícias agora."
    return "As notícias: " + " ".join(f"{x['titulo'].rstrip('.')}." for x in ns)


# ---------------------------------------------------------------- o resto

def melhorias():
    """Commits no repositório do Jarvis desde a meia-noite de ontem: (quantos, assuntos)."""
    r = subprocess.run(["git", "-C", REPO, "log", "--since=yesterday 00:00", "--format=%s"],
                       capture_output=True, text=True)
    assuntos = [l for l in r.stdout.splitlines() if l.strip()]
    return len(assuntos), assuntos[:5]


def carregar(nome):
    try:
        return json.load(open(os.path.join(CACHE, nome)))
    except (OSError, ValueError):
        return {}


def agenda(dia):
    evs = carregar("agenda.json").get("eventos") or []
    return [f"{e['inicio'][11:16]} {e['titulo']}" for e in sorted(evs, key=lambda e: e["inicio"])
            if not e["dia_inteiro"] and e["inicio"][:10] == dia.isoformat()]


def dados():
    """Tudo o que o bom dia pode usar, em texto para o Claude, e os cartões para a tela."""
    hoje = dt.date.today()
    c, ns = clima(), noticias()
    qtd, assuntos = melhorias()
    g = carregar("gitlab.json")
    mrs = [f"MR {m['iid']} ({m['threads_abertas']} thread(s) aberta(s), "
           f"{sum(1 for t in m.get('minhas') or [] if t['sua_vez'])} na vez dele)" for m in g.get("meus_mrs") or []]
    revs = [f"MR {r['iid']}: {r['status']}" for r in g.get("reviews") or []]
    tarefas = subprocess.run([os.path.join(BIN, "editar-tarefa.py"), "listar"], capture_output=True, text=True).stdout
    abertas = [l[2:] for l in tarefas.splitlines() if l.startswith("☐")]
    linhas = [
        f"HOJE: {['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo'][hoje.weekday()]}, "
        f"{hoje:%d/%m}, {dt.datetime.now():%H:%M}.",
        f"CLIMA: {frase_clima(c) if c else 'indisponível'}",
        f"AGENDA DE HOJE: {'; '.join(agenda(hoje)) or 'nenhuma reunião'}",
        f"AGENDA DE AMANHÃ: {'; '.join(agenda(hoje + dt.timedelta(days=1))) or 'nenhuma reunião'}",
        f"GITLAB: meus MRs: {'; '.join(mrs) or 'nenhum'}. Reviews para ele: {'; '.join(revs) or 'nenhum'}.",
        f"TAREFAS EM ABERTO: {'; '.join(abertas) or 'nenhuma'}",
        f"NOTÍCIAS: " + (" | ".join(f"[{x['tema']}] {x['titulo']}" for x in ns) or "indisponíveis"),
        f"MELHORIAS NO PRÓPRIO SISTEMA DESDE ONTEM: {qtd}" + (f" (por exemplo: {'; '.join(assuntos[:3])})" if qtd else ""),
    ]
    cartoes = {"gerado_em": time.time(), "clima": c, "agenda_hoje": agenda(hoje), "noticias": ns,
               "melhorias": qtd, "mrs": mrs, "reviews": revs, "tarefas": abertas}
    os.makedirs(os.path.dirname(CARTOES), exist_ok=True)
    json.dump(cartoes, open(CARTOES, "w"), ensure_ascii=False)
    return "\n".join(linhas)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "clima":
        print(frase_clima(quando=sys.argv[2] if len(sys.argv) > 2 else "hoje"))
    elif arg == "noticias":
        print(frase_noticias())
    else:
        print(dados())
