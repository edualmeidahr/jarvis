#!/usr/bin/env python3
"""Espelha a agenda da semana em notas de 07 Tarefas/Reunioes/, para o quadro Semana.

Lê ~/.claude/cache/agenda.json (gerado pelo agenda.py) — nunca chama a rede.

O script é dono da pasta: cria a reunião nova, reescreve a remarcada e apaga a
cancelada. Não edite essas notas; o que for escrito nelas some na próxima rodada.

Reunião não é tarefa. Não tem `feito`, não rola, e a rolagem nem olha esta pasta
(ela só lê 07 Tarefas/Notas). Se não rolasse separado, a daily de ontem, que
nunca vai ter `feito: true`, seria empurrada para hoje.

Duas travas:
- sem nenhuma coleta boa, não apaga nada: Google fora do ar não esvazia o quadro
- só mexe em nota com `tipo: reuniao`; qualquer outra coisa na pasta fica quieta
"""
import datetime as dt
import json
import os
import re
import sys

VAULT = os.path.expanduser(os.environ.get("VAULT", "~/Documentos/obsidian"))
CACHE = os.path.expanduser("~/.claude/cache/agenda.json")
PASTA = os.path.join(VAULT, "07 Tarefas", "Reunioes")
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta"]
PROIBIDOS = re.compile(r'[][\\/:*"<>|?#^]')  # o Obsidian recusa em nome de nota

# reunião fica acima das tarefas no quadro, em ordem de horário; dia inteiro no topo
ORDEM_BASE = -100000


def carregar():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def yaml_str(s):
    # JSON de uma string é YAML válido; resolve dois-pontos, aspas e acentos de uma vez
    return json.dumps(s, ensure_ascii=False)


def ocorrencias(ev, de, ate):
    """(data, hora_ini, hora_fim) de cada dia útil em que o evento aparece na semana."""
    if ev["dia_inteiro"]:
        ini, fim = dt.date.fromisoformat(ev["inicio"]), dt.date.fromisoformat(ev["fim"])
        d = max(ini, de)
        while d <= min(fim, ate):  # férias de segunda a sexta viram cinco cartões
            if d.weekday() < 5:
                yield d, None, None
            d += dt.timedelta(days=1)
        return
    ini, fim = dt.datetime.fromisoformat(ev["inicio"]), dt.datetime.fromisoformat(ev["fim"])
    if de <= ini.date() <= ate and ini.weekday() < 5:
        yield ini.date(), ini, fim


def nota(ev, dia, ini, fim):
    if ini:
        rotulo = f"{ini:%H:%M} {ev['titulo']}"
        hora = f"{ini:%H:%M}-{fim:%H:%M}"
        ordem = ORDEM_BASE + ini.hour * 60 + ini.minute
        nome = f"{dia:%Y-%m-%d} {ini:%Hh%M} {ev['titulo']}"
    else:
        rotulo = f"o dia todo · {ev['titulo']}"
        hora = "o dia todo"
        ordem = ORDEM_BASE - 1
        nome = f"{dia:%Y-%m-%d} dia todo {ev['titulo']}"

    linhas = [
        "---",
        "tipo: reuniao",
        f"evento: {yaml_str(ev['id'] + ('' if ini else '@' + dia.isoformat()))}",
        f"titulo: {yaml_str(rotulo)}",
        f"data: {dia.isoformat()}",
        f"dia: {DIAS[dia.weekday()]}",
        f"hora: {yaml_str(hora)}",
    ]
    if ev.get("link"):
        linhas.append(f"link: {yaml_str(ev['link'])}")
    linhas += ["tags:", "  - reuniao", f"kanban_order: {ordem}", "---", ""]
    if ev.get("link"):
        linhas.append(f"[Entrar na chamada]({ev['link']})")
    if ev.get("agenda"):
        linhas.append(f"[Abrir no Google Agenda]({ev['agenda']})")
    linhas += ["", "> Gerada pelo `reunioes.py` a partir da sua agenda. Não edite: some na próxima rodada."]

    arquivo = PROIBIDOS.sub("-", nome)[:180] + ".md"
    return arquivo, "\n".join(linhas) + "\n"


def eh_reuniao(caminho):
    try:
        cabeca = open(caminho, encoding="utf-8").read().split("---", 2)[1]
    except (OSError, IndexError):
        return False
    return re.search(r"^tipo:[ \t]*reuniao[ \t]*$", cabeca, re.M) is not None


def main():
    d = carregar()
    if not d or not d.get("gerado_em"):
        return 0  # sem coleta boa: não criar nem apagar nada

    de, ate = (dt.date.fromisoformat(x) for x in d["semana"])
    desejado = {}
    for ev in d.get("eventos") or []:
        for dia, ini, fim in ocorrencias(ev, de, ate):
            arquivo, conteudo = nota(ev, dia, ini, fim)
            if arquivo in desejado:  # dois eventos com mesmo título e hora: desempata pelo id
                arquivo = arquivo[:-3] + f" ({ev['id'][-6:]}).md"
            desejado[arquivo] = conteudo

    os.makedirs(PASTA, exist_ok=True)
    criadas = mudadas = apagadas = 0
    for nome in os.listdir(PASTA):
        caminho = os.path.join(PASTA, nome)
        if nome.endswith(".md") and nome not in desejado and eh_reuniao(caminho):
            os.remove(caminho)
            apagadas += 1

    for nome, conteudo in desejado.items():
        caminho = os.path.join(PASTA, nome)
        atual = open(caminho, encoding="utf-8").read() if os.path.exists(caminho) else None
        if atual == conteudo:
            continue
        with open(caminho + ".tmp", "w", encoding="utf-8") as f:
            f.write(conteudo)
        os.replace(caminho + ".tmp", caminho)
        criadas += atual is None
        mudadas += atual is not None

    if criadas or mudadas or apagadas:
        print(f"reuniões: {criadas} nova(s), {mudadas} alterada(s), {apagadas} removida(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
