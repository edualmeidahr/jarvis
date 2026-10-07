#!/usr/bin/env python3
"""Escreve no vault, para cada MR do gitlab.json, a nota com as suas threads.

Só lê o JSON que o gitlab.py gravou — nunca chama a rede.

Para cada MR (seus, reviews e outros em que você tem thread):
- acha a pasta do MR em 02 Issues: a da nota com `mr:` igual à URL do MR, senão
  a `02 Issues/<código>/`;
- se não existe e o MR é seu ou você é tester/code reviewer, cria a pasta;
  nos outros MRs (só te marcaram), sem pasta não escreve nada;
- grava `<prefixo> - Minhas threads do MR !<nº>.md`, regenerada a cada execução;
- grava `<prefixo> - Descrição do MR.md` só se ela não existe ou se foi este script
  que a criou (a que você escreveu à mão nunca é tocada).

Prefixo: o código da issue, ou "NO-ISSUE !<nº>" quando a branch não tem código.
As notas não têm `status`, então não entram no quadro do Issues.base.

    notas_mr.py           escreve as notas
    notas_mr.py --listar  só mostra o que escreveria
"""
import datetime as dt
import json
import os
import re
import sys

VAULT = os.path.expanduser(os.environ.get("VAULT", "~/Documentos/obsidian"))
ISSUES = os.path.join(VAULT, "02 Issues")
CACHE = os.path.expanduser("~/.claude/cache/gitlab.json")
MARCA = "<!-- gerada pelo notas_mr.py -->"
PAPEIS_QUE_CRIAM = ("Tester", "Code reviewer")


# ---------------------------------------------------------------- onde

def frontmatter(caminho):
    try:
        texto = open(caminho, encoding="utf-8").read()
    except OSError:
        return {}
    if not texto.startswith("---"):
        return {}
    cabeca = texto.split("---", 2)[1]
    return dict(re.findall(r"^(\w+):[ \t]*(.*?)[ \t]*$", cabeca, re.M))


def notas_por_mr():
    """URL do MR → pasta da nota que tem `mr:` com essa URL."""
    mapa = {}
    for pasta, _, arquivos in os.walk(ISSUES):
        for nome in arquivos:
            if nome.endswith(".md"):
                url = frontmatter(os.path.join(pasta, nome)).get("mr", "").rstrip("/")
                if url:
                    mapa.setdefault(url, pasta)
    return mapa


def nota_principal(pasta):
    """A nota da issue: a da pasta que tem `status` no frontmatter."""
    for nome in sorted(os.listdir(pasta)):
        if nome.endswith(".md") and frontmatter(os.path.join(pasta, nome)).get("status"):
            return nome[:-3]
    return None


def nome_de_pasta(mr):
    if mr.get("issue"):
        return mr["issue"]
    titulo = re.sub(r"^\s*NO[ -]ISSUE\s*[/:-]*\s*", "", mr["titulo"], flags=re.I)
    titulo = re.sub(r'[/\\:*?"<>|#^\[\]]', "-", titulo).strip(" -.")
    return f"NO-ISSUE - {titulo}"


def prefixo(mr):
    return mr.get("issue") or f"NO-ISSUE !{mr['iid']}"


def pasta_do_mr(mr, por_mr, pode_criar):
    """(pasta, criada?) ou (None, False) quando o MR não ganha nota."""
    pasta = por_mr.get(mr["url"].rstrip("/"))
    if pasta:
        return pasta, False
    pasta = os.path.join(ISSUES, nome_de_pasta(mr))
    if os.path.isdir(pasta):
        return pasta, False
    return (pasta, True) if pode_criar else (None, False)


def caminho_threads(pasta, mr):
    return os.path.join(pasta, f"{prefixo(mr)} - Minhas threads do MR !{mr['iid']}.md")


# ---------------------------------------------------------------- conteúdo

def quando(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d/%m %H:%M")


def citar(texto):
    return "\n".join(">" + (" " + linha if linha else "") for linha in texto.splitlines()) or ">"


def cabecalho(mr, pasta, papel):
    linhas = ["---"]
    if mr.get("issue"):
        linhas.append(f"issue: {mr['issue']}")
    linhas += [f"mr: {mr['url']}", f"atualizado: {dt.date.today().isoformat()}", "---", ""]
    return linhas


def linha_do_mr(mr, papel, pasta):
    linhas = [f"[{mr['ref']}]({mr['url']}) {mr['titulo']}", "",
              f"Branch: `{mr.get('branch') or '?'}` → `{mr.get('branch_alvo') or '?'}` · autor: {mr.get('autor') or '?'}",
              f"Seu papel: {papel}"]
    principal = nota_principal(pasta) if os.path.isdir(pasta) else None
    if principal:
        linhas.append(f"Issue: [[{principal}]]")
    return linhas


def bloco_thread(t, recuo=""):
    if t["comentario"]:
        nome, estado = "Comentário geral", "não se resolve"
    elif t["aberta"]:
        nome, estado = f"Thread {t['n']}", "aberta, **sua vez**" if t["sua_vez"] else "aberta, esperando o outro lado"
    else:
        nome, estado = f"Thread {t['n']}", "✓ resolvida"
    local = f"`{t['arquivo']}{':' + str(t['linha']) if t.get('linha') else ''}`" if t.get("arquivo") else "geral"
    linhas = [f"### [{nome}]({t['url']}) · {estado} · {t['origem']}", "", local, ""]
    for n in t["notas"]:
        quem = "você" if n["eu"] else n["autor"]
        linhas += [f"**{quem}** · {quando(n['quando'])}", citar(n["corpo"]), ""]
    return [recuo + l if l else recuo.rstrip() for l in linhas]


def resumo_em_tabela(threads):
    """Uma linha por thread: o mapa da nota. O detalhe vem nas seções de baixo."""
    linhas = ["## Resumo", "", "| Nº | Local | Estado | Origem | Msgs | Última |", "| --- | --- | --- | --- | --- | --- |"]
    for t in threads:
        if t["comentario"]:
            nome, estado = "geral", "comentário"
        elif t["aberta"]:
            nome, estado = str(t["n"]), "**sua vez**" if t["sua_vez"] else "aberta"
        else:
            nome, estado = str(t["n"]), "✓ resolvida"
        ultima = t["notas"][-1]
        quem = "você" if ultima["eu"] else ultima["autor"].split()[0]
        linhas.append(f"| [{nome}]({t['url']}) | `{t['local']}` | {estado} | {t['origem']} | {len(t['notas'])} | "
                      f"{quem}, {quando(ultima['quando'])} |")
    return linhas


def nota_threads(mr, pasta, papel):
    threads = mr.get("minhas") or []
    abertas = [t for t in threads if t["aberta"]]
    comentarios = [t for t in threads if t["comentario"]]
    resolvidas = [t for t in threads if not t["aberta"] and not t["comentario"]]
    regra = "todas as threads, porque o MR é seu" if papel == "autor" else "as threads que você criou ou em que te marcaram"
    linhas = cabecalho(mr, pasta, papel) + [f"# Minhas threads do MR !{mr['iid']}", ""]
    linhas += linha_do_mr(mr, papel, pasta) + [""]
    linhas += [f"> Gerada pelo `notas_mr.py` toda manhã (e a cada `manha.sh`), com {regra}. "
               "É sobrescrita inteira: anote na nota principal da issue.", ""]
    if threads:
        linhas += resumo_em_tabela(threads) + [""]
    sua_vez = sum(t["sua_vez"] for t in threads)
    linhas += [f"## Abertas ({len(abertas)}{f', {sua_vez} sua vez' if sua_vez else ''})", ""]
    for t in abertas:
        linhas += bloco_thread(t)
    if not abertas:
        linhas += ["Nenhuma.", ""]
    if comentarios:
        linhas += [f"## Comentários gerais ({len(comentarios)})", ""]
        for t in comentarios:
            linhas += bloco_thread(t)
    if resolvidas:
        linhas += [f"> [!success]- Resolvidas ({len(resolvidas)})", ">"]
        for t in resolvidas:
            linhas += bloco_thread(t, recuo="> ")
    return "\n".join(linhas).rstrip() + "\n\n" + MARCA + "\n"


def nota_descricao(mr, pasta, papel):
    linhas = cabecalho(mr, pasta, papel) + [f"# Descrição do MR !{mr['iid']}", ""]
    linhas += linha_do_mr(mr, papel, pasta) + ["", "---", ""]
    corpo = mr.get("descricao", "").replace("](/uploads/", f"]({mr['url'].split('/-/merge_requests/')[0]}/uploads/")
    corpo = re.sub(r"!\[([^\]]*)\]\(([^)]*)\)(\{[^}]*\})?", r"[\1](\2)", corpo)
    linhas.append(corpo.strip() or "O autor não escreveu descrição.")
    return "\n".join(linhas).rstrip() + "\n\n" + MARCA + "\n"


# ---------------------------------------------------------------- gravar

def gravar(caminho, texto, so_listar):
    """Grava só se mudou. Devolve o que fez, para o log."""
    if os.path.exists(caminho):
        antigo = open(caminho, encoding="utf-8").read()
        # "atualizado:" muda todo dia; não reescreve à toa (o Obsidian percebe e sincroniza)
        sem_data = lambda s: re.sub(r"^atualizado: .*$", "", s, count=1, flags=re.M)
        if sem_data(antigo) == sem_data(texto):
            return None
    if so_listar:
        return "escreveria"
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, caminho)
    return "gravada"


def alvos(d):
    """(mr, papel, pode criar a pasta?) para cada MR do cache."""
    for mr in d.get("meus_mrs") or []:
        yield mr, "autor", True
    for mr in d.get("reviews") or []:
        yield mr, mr["papeis"], any(p in mr["papeis"] for p in PAPEIS_QUE_CRIAM)
    for mr in d.get("outros_mrs") or []:
        yield mr, "te marcaram ou você comentou", False


def main():
    so_listar = "--listar" in sys.argv
    try:
        d = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        print("notas_mr: sem gitlab.json", file=sys.stderr)
        return 0
    por_mr = notas_por_mr()
    for mr, papel, pode_criar in alvos(d):
        pasta, criada = pasta_do_mr(mr, por_mr, pode_criar)
        if not pasta:
            continue
        rel = os.path.relpath(pasta, VAULT)
        if criada:
            print(f"notas_mr: {'criaria' if so_listar else 'pasta nova'} {rel}")
        feito = gravar(caminho_threads(pasta, mr), nota_threads(mr, pasta, papel), so_listar)
        if feito:
            print(f"notas_mr: {mr['ref']} threads {feito} em {rel}")
        descricao = os.path.join(pasta, f"{prefixo(mr)} - Descrição do MR.md")
        if not os.path.exists(descricao) or MARCA in open(descricao, encoding="utf-8").read():
            feito = gravar(descricao, nota_descricao(mr, pasta, papel), so_listar)
            if feito:
                print(f"notas_mr: {mr['ref']} descrição {feito} em {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
