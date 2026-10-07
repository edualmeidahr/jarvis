#!/usr/bin/env python3
"""Desenha o que o gitlab.py coletou. Só lê o JSON — nunca chama a rede.

    painel.py              reescreve o Painel.md (dashboard) na raiz do vault
    painel.py --diaria     põe a foto do GitLab no topo da diária de hoje
    painel.py --resumo     duas ou três linhas, para o SessionStart
    painel.py --notificar  notificação do GNOME com o mesmo resumo

Duas telas, dois papéis:

- **Painel.md** é o dashboard: números do GitLab e os kanbans embutidos. É
  gerado inteiro — o que for editado nele some.
- **A diária** ganha no topo uma *foto* dos MRs e reviews, entre marcadores.
  Rodar de novo só troca o que está entre eles; Foco, Aconteceu, Aprendi e
  Amanhã nunca são tocados. Só a diária de **hoje** é atualizada — as dos
  outros dias ficam congeladas, e é isso que as torna log.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys

VAULT = os.path.expanduser(os.environ.get("VAULT", "~/Documentos/obsidian"))
CACHE = os.path.expanduser("~/.claude/cache/gitlab.json")
AGENDA = os.path.expanduser("~/.claude/cache/agenda.json")
PAINEL = os.path.join(VAULT, "Painel.md")
TEMPLATE_DIARIA = os.path.join(VAULT, "06 Templates", "Diária.md")
VELHO_HORAS = 20  # mais que isso, o dado é de ontem

INICIO = "<!-- gitlab:inicio · gerado pelo painel.py — o que estiver entre os marcadores é reescrito -->"
FIM = "<!-- gitlab:fim -->"
# engole as linhas em branco depois do fim; senão cada execução deixa uma a mais
BLOCO = re.compile(r"<!-- gitlab:inicio.*?-->.*?<!-- gitlab:fim -->\n*", re.S)


def carregar():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def tem_dado(d):
    return bool(d and d.get("gerado_em"))


def idade_horas(d):
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(d["gerado_em"])).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        return None


def hora(iso):
    return dt.datetime.fromisoformat(iso).strftime("%d/%m %H:%M")


def threads(n):
    return "nenhuma thread aberta" if n == 0 else "1 thread aberta" if n == 1 else f"{n} threads abertas"


def link(mr):
    return f"[{mr['ref']}]({mr['url']}) {mr['titulo']}"


def caminho_diaria(dia):
    # mesmo formato do daily-notes.json: 01 Diário/YYYY/MM/YYYY-MM-DD
    return os.path.join(VAULT, "01 Diário", f"{dia:%Y}", f"{dia:%m}", f"{dia:%Y-%m-%d}.md")


def link_diaria(dia, texto):
    return f"[[01 Diário/{dia:%Y}/{dia:%m}/{dia:%Y-%m-%d}|{texto}]]"


def notas_de_issue():
    """Código e URL do MR → nome da nota rastreável (a que tem `status`), para o wikilink bater.

    O link tem que ser o nome real do arquivo: `[[MLH037260]]` não acha a nota
    `MLH037260 - Embutido…` e, se clicado, cria uma nota vazia.
    A URL do campo `mr:` vem primeiro: é a única chave de MR NO-ISSUE, cujo
    código não identifica nota nenhuma.
    """
    mapa = {}
    for pasta, _, arquivos in os.walk(os.path.join(VAULT, "02 Issues")):
        for nome in arquivos:
            if not nome.endswith(".md"):
                continue
            try:
                cabeca = open(os.path.join(pasta, nome), encoding="utf-8").read().split("---", 2)[1]
            except (OSError, IndexError):
                continue
            if not re.search(r"^status:[ \t]*\S", cabeca, re.M):
                continue
            cod = re.search(r"^issue:[ \t]*(\S+)", cabeca, re.M)
            if cod and cod.group(1) != "NO-ISSUE":
                mapa.setdefault(cod.group(1), nome[:-3])
            mr = re.search(r"^mr:[ \t]*(\S+)", cabeca, re.M)
            if mr:
                mapa.setdefault(mr.group(1).rstrip("/"), nome[:-3])
    return mapa


NOTAS = {}


def issue(mr):
    nota = NOTAS.get(mr["url"].rstrip("/")) or NOTAS.get(mr.get("issue") or "")
    return f" · [[{nota}|{mr.get('issue') or 'nota'}]]" if nota else ""


import importlib.util as _iu
_spec = _iu.spec_from_file_location("notas_mr", os.path.join(os.path.dirname(os.path.abspath(__file__)), "notas_mr.py"))
notas_mr = _iu.module_from_spec(_spec)
_spec.loader.exec_module(notas_mr)

def em_uma_linha(corpo):
    """O comentário inteiro numa linha, para caber num item de lista."""
    corpo = re.sub(r"```suggestion[^\n]*\n(.*?)```",
                   lambda m: "[sugestão: `" + " ⏎ ".join(l.strip() for l in m.group(1).strip().splitlines()) + "`]",
                   corpo, flags=re.S)
    corpo = re.sub(r"```[^\n]*\n(.*?)```",
                   lambda m: "`" + " ⏎ ".join(l.strip() for l in m.group(1).strip().splitlines()) + "`",
                   corpo, flags=re.S)
    return re.sub(r"\s+", " ", corpo).strip()


def quando(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d/%m %H:%M")


def link_nota_threads(mr):
    """Wikilink para a nota que o notas_mr.py gravou, se ela existe."""
    pasta, _ = notas_mr.pasta_do_mr(mr, POR_MR, pode_criar=False)
    if not pasta:
        return ""
    caminho = notas_mr.caminho_threads(pasta, mr)
    return f" · [[{os.path.basename(caminho)[:-3]}|minhas threads]]" if os.path.exists(caminho) else ""


def celula(texto, n=None):
    texto = em_uma_linha(texto).replace("|", "\\|")
    return texto[:n] + "…" if n and len(texto) > n else texto


def primeiro_nome(nome):
    return nome.split()[0] if nome else "?"


def tabela_threads(d):
    """Uma linha por thread aberta sua, de todos os MRs; quem espera você vem primeiro.
    A conversa inteira fica num callout recolhido logo abaixo."""
    abertas = [(mr, t) for grupo in ("meus_mrs", "reviews", "outros_mrs") for mr in d.get(grupo) or []
               for t in mr.get("minhas") or [] if t["aberta"]]
    if not abertas:
        return []
    abertas.sort(key=lambda par: (not par[1]["sua_vez"], par[0]["iid"], par[1]["n"]))
    sua_vez = sum(t["sua_vez"] for _, t in abertas)
    linhas = ["", f"## Threads abertas ({len(abertas)}{f', {sua_vez} sua vez' if sua_vez else ''})", "",
              "| MR | Nº | Local | Vez de | Última mensagem |", "| --- | --- | --- | --- | --- |"]
    for mr, t in abertas:
        ultima = t["notas"][-1]
        quem = "você" if ultima["eu"] else primeiro_nome(ultima["autor"])
        outro = next((n["autor"] for n in reversed(t["notas"]) if not n["eu"]), None)
        vez = "**você**" if t["sua_vez"] else primeiro_nome(outro) if outro else "revisores"
        linhas.append(f"| [!{mr['iid']}]({mr['url']}) | [{t['n']}]({t['url']}) | `{celula(t['local'])}` | {vez} | "
                      f"**{quem}** {quando(ultima['quando'])}: {celula(ultima['corpo'], 140)} |")
    linhas += ["", f"> [!quote]- Conversas completas ({len(abertas)})"]
    for mr, t in abertas:
        linhas += [">", f"> **!{mr['iid']} · [Thread {t['n']}]({t['url']})** · `{t['local']}` · {t['origem']}"]
        for n in t["notas"]:
            quem = "você" if n["eu"] else n["autor"]
            linhas.append(f"> - **{quem}** · {quando(n['quando'])} — {em_uma_linha(n['corpo'])}")
    return linhas + [""]


POR_MR = {}


# ---------------------------------------------------------------- pedaços

def aviso(d):
    """O que dizer quando o dado não é de agora. Log que mente é pior que log vazio."""
    if not tem_dado(d):
        motivo = (d or {}).get("erro") or "o gitlab.py ainda não rodou"
        return [f"> [!warning] Sem dado do GitLab: {motivo}.",
                "> Guarde o token e rode `~/.claude/bin/manha.sh`."]
    if d.get("erro"):
        return [f"> [!warning] GitLab falhou às {hora(d['tentado_em'])}: {d['erro']}.",
                f"> O que aparece aqui é a coleta de **{hora(d['gerado_em'])}**."]
    return []


def listas_gitlab(d):
    linhas = ["## Meus MRs", ""]
    mrs = d.get("meus_mrs") or []
    if not mrs:
        linhas.append("- Nenhum MR aberto.")
    for mr in mrs:
        rascunho = " *(draft)*" if mr.get("draft") else ""
        linhas.append(f"- {link(mr)}{rascunho}{issue(mr)}")
        linhas.append(f"\t- {threads(mr['threads_abertas'])}{link_nota_threads(mr)}")

    linhas += ["", "## Reviews", ""]
    revs = d.get("reviews") or []
    if not revs:
        linhas.append("- Nenhum review pendente.")
    for r in revs:
        status = r["status"]
        if status == "reavaliar threads":
            status += f", {threads(r['threads_abertas'])}"
        linhas.append(f"- {link(r)} ({r['autor']}){issue(r)}")
        linhas.append(f"\t- {r['papeis']}: {status}{link_nota_threads(r)}")

    outros = [o for o in d.get("outros_mrs") or [] if any(t["aberta"] for t in o["minhas"])]
    if outros:
        linhas += ["", "## Outros MRs com thread sua", ""]
    for o in outros:
        linhas.append(f"- {link(o)} ({o['autor']}){issue(o)}{link_nota_threads(o)}")
    return linhas + tabela_threads(d)


# ---------------------------------------------------------------- telas

def painel(d):
    hoje = dt.date.today()
    linhas = [
        "---",
        "tipo: painel",
        "cssclasses:",
        "  - painel-largo",
        "---",
        "",
        f"> Gerado em {dt.datetime.now():%d/%m %H:%M}. **Não edite** — some na próxima execução. "
        f"Atualizar: `~/.claude/bin/manha.sh`",
        "",
    ]
    linhas += aviso(d)

    if tem_dado(d):
        mrs = d.get("meus_mrs") or []
        revs = d.get("reviews") or []
        linhas += [
            "",
            f"| MRs abertos | threads nos seus MRs | reviews esperando você | foto de |",
            "|:-:|:-:|:-:|:-:|",
            f"| **{len(mrs)}** | **{sum(m['threads_abertas'] for m in mrs)}** | **{len(revs)}** "
            f"| {hora(d['gerado_em'])} |",
            "",
            f"Detalhe de cada MR na {link_diaria(hoje, 'diária de hoje')}.",
        ]

    linhas += [
        "",
        "## Issues",
        "",
        "![[02 Issues/Issues.base#Quadro]]",
        "",
        "## Semana",
        "",
        "![[07 Tarefas/Semana.base#Semana]]",
        "",
        "## Emperradas",
        "",
        "![[07 Tarefas/Semana.base#Emperradas]]",
        "",
    ]
    return "\n".join(linhas) + "\n"


def bloco_diaria(d):
    linhas = [INICIO]
    if tem_dado(d):
        linhas.append(f"*Foto do GitLab às {hora(d['gerado_em'])}.*")
    linhas += aviso(d)
    if tem_dado(d):
        linhas += [""] + listas_gitlab(d)
    linhas += [FIM, ""]
    return "\n".join(linhas) + "\n"


def atualizar_diaria(d, dia):
    """Cria a diária pelo template se faltar; troca só o bloco entre marcadores."""
    caminho = caminho_diaria(dia)
    if os.path.exists(caminho):
        texto = open(caminho, encoding="utf-8").read()
    else:
        try:
            texto = open(TEMPLATE_DIARIA, encoding="utf-8").read()
        except OSError:
            texto = "## Foco\n\n## Aconteceu\n\n## Aprendi\n\n## Amanhã\n"

    bloco = bloco_diaria(d)
    novo = BLOCO.sub(lambda _: bloco, texto, count=1) if BLOCO.search(texto) else bloco + texto
    if novo == texto:
        return caminho, False  # não mexe no arquivo à toa: o Obsidian percebe e sincroniza

    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(novo)
    os.replace(tmp, caminho)
    return caminho, True


def resumo(d):
    """O que precisa de ação. Silêncio quando não há nada — não polui a sessão."""
    if d is None:
        return []
    out = []
    com_thread = [m for m in d.get("meus_mrs") or [] if m["threads_abertas"]]
    if com_thread:
        out.append("GitLab — seus MRs com thread aberta: " +
                   ", ".join(f"{m['ref']} ({m['threads_abertas']})" for m in com_thread))
    for r in d.get("reviews") or []:
        out.append(f"GitLab — {r['ref']} {r['papeis']}: {r['status']}")
    sua_vez = [f"{m['ref']} ({', '.join(str(t['n']) for t in m['minhas'] if t['sua_vez'])})"
               for grupo in ("meus_mrs", "reviews", "outros_mrs") for m in d.get(grupo) or []
               if any(t["sua_vez"] for t in m.get("minhas") or [])]
    if sua_vez:
        out.append("GitLab — sua vez de responder: " + ", ".join(sua_vez))
    h = idade_horas(d)
    if d.get("erro"):
        out.append(f"GitLab — atenção: {d['erro']}")
    elif h is not None and h > VELHO_HORAS:
        out.append(f"GitLab — dado de {int(h)}h atrás; o timer das 7h pode não ter rodado")
    return out + resumo_agenda()


def resumo_agenda():
    """As reuniões de hoje numa linha. Sem configurar, fica calado: você já sabe."""
    try:
        with open(AGENDA, encoding="utf-8") as f:
            a = json.load(f)
    except (OSError, ValueError):
        return []
    erro = a.get("erro") or ""
    if erro and "não configurada" not in erro:
        return [f"Agenda — atenção: {erro}"]
    hoje = dt.date.today().isoformat()
    do_dia = []
    for ev in a.get("eventos") or []:
        if ev["dia_inteiro"]:
            if ev["inicio"] <= hoje <= ev["fim"]:
                do_dia.append(f"o dia todo {ev['titulo']}")
        elif ev["inicio"][:10] == hoje:
            do_dia.append(f"{ev['inicio'][11:16]} {ev['titulo']}")
    return [f"Agenda hoje — {' · '.join(do_dia)}"] if do_dia else []


# ---------------------------------------------------------------- main

def main():
    NOTAS.update(notas_de_issue())
    POR_MR.update(notas_mr.notas_por_mr())
    d = carregar()
    modo = sys.argv[1] if len(sys.argv) > 1 else ""

    if modo == "--resumo":
        print("\n".join(resumo(d)))
        return 0

    if modo == "--notificar":
        linhas = resumo(d)
        if linhas:
            corpo = "\n".join(l.removeprefix("GitLab — ") for l in linhas)
            subprocess.run(["notify-send", "-a", "Jarvis", "Bom dia — GitLab", corpo], check=False)
        return 0

    if modo == "--diaria":
        caminho, mudou = atualizar_diaria(d, dt.date.today())
        if mudou:
            print(f"diária: {os.path.relpath(caminho, VAULT)}")
        return 0

    tmp = PAINEL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(painel(d))
    os.replace(tmp, PAINEL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
