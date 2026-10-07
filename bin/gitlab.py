#!/usr/bin/env python3
"""Junta no GitLab os seus MRs abertos e os reviews que o bot te atribuiu.

Não escreve no vault. Grava um JSON em ~/.claude/cache/gitlab.json e cada tela
lê dele: o Painel.md, o /fechamento, o resumo do SessionStart e, depois, o HUD.

A lógica e as chamadas de API vêm do sydle-daily, que o time já validou contra
o bot de verdade. O que muda é a saída: dado estruturado em vez de markdown.

Token: `read_api`, guardado no chaveiro GNOME. Nunca em arquivo nem em variável.
    ~/.local/bin/secret-tool store --label=GitLab service sydle-daily account gitlab

Saída: 0 se deu certo ou se falta o token (tentar de novo não ajuda);
       1 se a rede ou a API falharam (o systemd tenta de novo).
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import importlib.util as _iu
_spec_c = _iu.spec_from_file_location("config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py"))
config = _iu.module_from_spec(_spec_c)
_spec_c.loader.exec_module(config)  # ~/.config/jarvis/local.env: o que é da empresa fica fora do repositório

GITLAB_URL = config.get("GITLAB_URL")
GITLAB_USER = config.get("GITLAB_USER")
GITLAB_PROJECT_ID = config.get("GITLAB_PROJECT_ID")
REVIEWERS_BOT = config.get("GITLAB_REVIEWERS_BOT")

SECRET_TOOL = os.path.expanduser("~/.local/bin/secret-tool")
CACHE = os.path.expanduser("~/.claude/cache/gitlab.json")
# NO-ISSUE não entra: não identifica nada, e casava com qualquer nota `issue: NO-ISSUE` do vault.
CODIGO = re.compile(r"\b([A-Z]{2,4}\d{5,6})\b")
BOT = re.compile(r"^project_\d+_bot_")


# ---------------------------------------------------------------- acesso

def ler_token():
    try:
        r = subprocess.run([SECRET_TOOL, "lookup", "service", "sydle-daily", "account", "gitlab"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip()


def api(token, caminho):
    req = urllib.request.Request(f"{GITLAB_URL}/api/v4/{caminho}", headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp), resp.headers.get("X-Next-Page", "")


def api_todas(token, caminho):
    """Segue a paginação do GitLab até o fim."""
    sep = "&" if "?" in caminho else "?"
    tudo, pagina = [], "1"
    while pagina:
        corpo, pagina = api(token, f"{caminho}{sep}per_page=100&page={pagina}")
        tudo.extend(corpo)
    return tudo


# ---------------------------------------------------------------- regras

def threads_abertas(discussoes, so_de=None):
    """Discussões com nota resolvível ainda aberta. `so_de` filtra quem abriu.

    As threads do bot que atribui papel ("Code reviewer: @fulano") ficam de fora:
    elas nascem abertas em todo MR e não são revisão. Sem isso, um MR que ninguém
    olhou aparecia com 3 threads abertas.
    """
    n = 0
    for d in discussoes:
        notas = d.get("notes") or []
        if notas and notas[0]["author"]["username"] == REVIEWERS_BOT:
            continue
        if so_de and (not notas or notas[0]["author"]["username"] != so_de):
            continue
        if any(x.get("resolvable") and not x.get("resolved") for x in notas):
            n += 1
    return n


def marcacoes_do_bot(discussoes, eu, bot):
    """As notas em que o bot te marca. Ele abre uma thread por papel: `Tester: @fulano`."""
    mencao = re.compile("@" + re.escape(eu) + r"(?![\w.-])")
    return [n for d in discussoes for n in d.get("notes") or []
            if n["author"]["username"] == bot and mencao.search(n.get("body", ""))]


def classificar(discussoes, aprovado, eu, bot):
    """None se o review não é seu ou já acabou; senão (papéis, status, threads abertas).

    Mesma tabela do sydle-daily. O MR só sai da lista quando a thread do bot que
    te marca está resolvida E você deu approve.
    """
    marcas = marcacoes_do_bot(discussoes, eu, bot)
    if not marcas:
        return None

    papeis = " e ".join(sorted({re.sub(r":\s*@.*$", "", m["body"], flags=re.S).strip() for m in marcas}))
    thread_bot_aberta = any(not m.get("resolved") for m in marcas)
    if not thread_bot_aberta and aprovado:
        return None

    comentou = sum(1 for d in discussoes for n in d.get("notes") or []
                   if not n.get("system") and n["author"]["username"] == eu)
    minhas_abertas = threads_abertas(discussoes, so_de=eu)

    if comentou == 0:
        status = "primeira revisão"
    elif minhas_abertas > 0:
        status = "reavaliar threads"
    elif aprovado:
        status = "aprovado, falta resolver a thread do bot"
    elif not thread_bot_aberta:
        status = "thread do bot resolvida, falta aprovar"
    else:
        status = "threads resolvidas, avaliar aprovação"
    return papeis, status, minhas_abertas


def numeradas(discussoes):
    """(nº, discussão, notas) na numeração do /threads (mr-threads.sh), para dizer "confere a 6".

    Conta por ordem de criação, resolvidas incluídas, então o número não muda. Fora:
    discussão sem nota resolvível, notas de sistema e de bot.
    """
    lista = []
    for d in discussoes:
        notas_d = d.get("notes") or []
        if not any(x.get("resolvable") for x in notas_d):
            continue
        notas = [x for x in notas_d if not x.get("system") and not BOT.search(x["author"]["username"])]
        if notas:
            lista.append((notas[0]["id"], d, notas))
    lista.sort(key=lambda t: t[0])
    return [(i + 1, d, notas) for i, (_, d, notas) in enumerate(lista)]


def corpo_para_vault(corpo, url_projeto):
    """Markdown do GitLab pronto para o Obsidian: anexo com URL absoluta, imagem vira link
    (o anexo só abre com login) e "#123" não vira tag."""
    corpo = corpo.replace("](/uploads/", f"]({url_projeto}/uploads/")
    corpo = re.sub(r"!\[([^\]]*)\]\(([^)]*)\)(\{[^}]*\})?", r"[\1](\2)", corpo)
    return re.sub(r"(^|\s)#(?=\w)", "\\1#\u200b", corpo).strip()


def minhas_threads(discussoes, eu, url_mr, todas=False):
    """Threads que você criou ou em que te marcaram com @ em algum momento, com cada comentário.

    `todas`: no seu próprio MR entra tudo, porque toda thread ali é com você.
    Só menção direta: @grupo (o time todo) não conta, senão todo MR de
    fechamento de versão entraria inteiro. Abertas primeiro.
    """
    mencao = re.compile("@" + re.escape(eu) + r"(?![\w.-])")
    url_projeto = url_mr.split("/-/merge_requests/")[0]
    numero = {id(d): n for n, d, _ in numeradas(discussoes)}
    saida = []
    for d in discussoes:
        notas = [x for x in d.get("notes") or [] if not x.get("system") and not BOT.search(x["author"]["username"])]
        if not notas:
            continue
        n = numero.get(id(d))  # None: comentário geral, que não se resolve e o /threads não numera
        criou = notas[0]["author"]["username"] == eu
        marcado = any(x["author"]["username"] != eu and mencao.search(x.get("body", "")) for x in notas)
        if not (todas or criou or marcado):
            continue
        aberta = any(x.get("resolvable") and not x.get("resolved") for x in d["notes"])
        pos = notas[0].get("position") or {}
        arquivo = pos.get("new_path") or pos.get("old_path")
        linha = pos.get("new_line") or pos.get("old_line")
        saida.append({
            "n": n,
            "url": f"{url_mr}#note_{notas[0]['id']}",
            "arquivo": arquivo,
            "local": (arquivo.split("/")[-1] + (f":{linha}" if linha else "")) if arquivo else "geral",
            "linha": linha,
            "aberta": aberta,
            "comentario": n is None,
            "origem": "você criou" if criou else "te marcaram" if marcado else "no seu MR",
            # sua vez: aberta e a última palavra não é sua
            "sua_vez": aberta and notas[-1]["author"]["username"] != eu,
            "notas": [{"autor": x["author"]["name"], "eu": x["author"]["username"] == eu,
                       "quando": x["created_at"], "corpo": corpo_para_vault(x.get("body", ""), url_projeto)}
                      for x in notas],
        })
    saida.sort(key=lambda t: (not t["aberta"], t["comentario"], t["n"] or 0, t["notas"][0]["quando"]))
    return saida


def issue_do_mr(mr):
    for texto in (mr.get("source_branch", ""), mr.get("title", "")):
        m = CODIGO.search(texto or "")
        if m:
            return m.group(1)
    return None


def resumo_mr(mr):
    projeto = mr["references"]["full"].split("!")[0].split("/")[-1]
    return {
        "ref": f"{projeto}!{mr['iid']}",
        "iid": mr["iid"],
        "titulo": mr["title"],
        "url": mr["web_url"],
        "branch": mr.get("source_branch"),
        "issue": issue_do_mr(mr),
        "draft": bool(mr.get("draft") or mr.get("work_in_progress")),
        "autor": mr["author"]["name"],
        "branch_alvo": mr.get("target_branch"),
        "descricao": mr.get("description") or "",
    }


# ---------------------------------------------------------------- coleta

def meus_mrs(token):
    saida = []
    for mr in api_todas(token, "merge_requests?scope=created_by_me&state=opened"):
        disc = api_todas(token, f"projects/{mr['project_id']}/merge_requests/{mr['iid']}/discussions")
        saida.append({**resumo_mr(mr), "threads_abertas": threads_abertas(disc),
                      "minhas": minhas_threads(disc, GITLAB_USER, mr["web_url"], todas=True)})
    return saida


def reviews(token):
    """Os reviews que o bot te deu e, à parte, os outros MRs com thread sua.

    "Sua" = você criou ou te marcaram. As discussões de todo MR aberto já vêm para
    achar a marcação do bot; essas threads saem do mesmo dado, sem chamada a mais.
    """
    saida, outros = [], []
    filtro = urllib.parse.quote("not[author_username]")
    base = f"projects/{GITLAB_PROJECT_ID}/merge_requests"
    for mr in api_todas(token, f"{base}?state=opened&{filtro}={GITLAB_USER}"):
        caminho = f"{base}/{mr['iid']}"
        disc = api_todas(token, f"{caminho}/discussions")
        minhas = minhas_threads(disc, GITLAB_USER, mr["web_url"])
        c = None
        if marcacoes_do_bot(disc, GITLAB_USER, REVIEWERS_BOT):  # poupa a chamada de approvals nos outros
            aprov, _ = api(token, f"{caminho}/approvals")
            aprovado = GITLAB_USER in [a["user"]["username"] for a in aprov.get("approved_by") or []]
            c = classificar(disc, aprovado, GITLAB_USER, REVIEWERS_BOT)
        if c:
            papeis, status, abertas = c
            saida.append({**resumo_mr(mr), "papeis": papeis, "status": status,
                          "threads_abertas": abertas, "minhas": minhas})
        elif minhas:
            outros.append({**resumo_mr(mr), "minhas": minhas})
    return saida, outros


# ---------------------------------------------------------------- saída

def gravar(dados):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE)  # quem lê nunca pega o arquivo pela metade


def anterior():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main():
    agora = dt.datetime.now().isoformat(timespec="seconds")
    token = ler_token()
    if not token:
        gravar({**anterior(), "erro": "token do GitLab não está no chaveiro", "tentado_em": agora})
        print("gitlab: sem token no chaveiro", file=sys.stderr)
        return 0

    try:
        revs, outros = reviews(token)
        dados = {"gerado_em": agora, "erro": None,
                 "meus_mrs": meus_mrs(token), "reviews": revs, "outros_mrs": outros}
    except urllib.error.HTTPError as e:
        motivo = "token inválido ou expirado" if e.code == 401 else f"API respondeu {e.code}"
        # 401 não melhora tentando de novo; o resto pode ser instabilidade
        gravar({**anterior(), "erro": motivo, "tentado_em": agora})
        print(f"gitlab: {motivo}", file=sys.stderr)
        return 0 if e.code == 401 else 1
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # mantém o último dado bom e marca que está velho
        gravar({**anterior(), "erro": f"sem rede: {e}", "tentado_em": agora})
        print(f"gitlab: sem rede ({e})", file=sys.stderr)
        return 1

    gravar(dados)
    print(f"gitlab: {len(dados['meus_mrs'])} MR(s), {len(dados['reviews'])} review(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
