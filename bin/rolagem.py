#!/usr/bin/env python3
"""Rola tarefa não feita para o próximo dia útil e mantém `dia` e `data` de acordo.

Duas verdades seriam um desastre, então só existe uma: `data` (ISO). O `dia`
(segunda…sexta) é o rótulo que o quadro usa como coluna, e é sempre derivado.

Mas o quadro escreve no `dia` quando você arrasta um cartão. Por isso o script
começa **reconciliando**: se o `dia` discorda do `data`, quem mandou foi o
arrasto, e o `data` é recalculado a partir dele. Só depois vem a rolagem.

Regra da rolagem: tarefa com `feito: false` e `data` no passado passa a valer
hoje — ou, se hoje for fim de semana, na segunda. Sexta não terminada cai na
segunda pelo mesmo caminho, sem caso especial.

Cada rolagem incrementa `rolou`. Arrastar não incrementa: mudar de ideia não é
escorregar. Quem chega a 3 aparece no aviso.
"""
import datetime as dt
import io
import os
import re
import sys

VAULT = os.path.expanduser(os.environ.get("VAULT", "~/Documentos/obsidian"))
PASTA = os.path.join(VAULT, "07 Tarefas", "Notas")
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta"]
EMPERRADA = 3


def dia_util(d):
    """O próprio dia, ou a segunda seguinte se cair no fim de semana."""
    return d + dt.timedelta(days=7 - d.weekday()) if d.weekday() >= 5 else d


def data_do_dia(rotulo, hoje):
    """A data do rótulo (`terça`) na semana corrente; se já passou, na próxima."""
    i = DIAS.index(rotulo)
    alvo = hoje - dt.timedelta(days=hoje.weekday()) + dt.timedelta(days=i)
    return alvo + dt.timedelta(days=7) if alvo < hoje else alvo


def campo(texto, nome):
    m = re.search(r"^%s:[ \t]*(.*)$" % nome, texto, re.M)
    return m.group(1).strip() if m else None


def troca(texto, nome, valor):
    if re.search(r"^%s:" % nome, texto, re.M):
        return re.sub(r"^(%s:)[ \t]*.*$" % nome, r"\1 %s" % valor, texto, count=1, flags=re.M)
    return texto.rstrip("\n") + "\n%s: %s\n" % (nome, valor)


def main():
    if not os.path.isdir(PASTA):
        return 0

    hoje = dt.date.today()
    alvo = dia_util(hoje)
    movidas, arrastadas, emperradas, datadas = [], [], [], []

    for nome in sorted(os.listdir(PASTA)):
        if not nome.endswith(".md"):
            continue
        caminho = os.path.join(PASTA, nome)
        s = io.open(caminho, encoding="utf-8").read()
        if not s.startswith("---"):
            continue

        cabeca = s.split("---", 2)[1]
        if campo(cabeca, "tipo") != "tarefa":
            continue
        if (campo(cabeca, "feito") or "false").lower() == "true":
            continue

        titulo = nome[:-3]
        rolou = int(campo(cabeca, "rolou") or 0)
        rotulo = (campo(cabeca, "dia") or "").strip()

        try:
            data = dt.date.fromisoformat(campo(cabeca, "data") or "")
        except ValueError:
            # cartão criado pelo + do quadro: o plugin grava o `dia` (a coluna) e deixa o
            # `data` vazio. Sem isto, ele nunca rolaria e sumiria do "o que temos pra hoje".
            if rotulo not in DIAS:
                continue
            data = data_do_dia(rotulo, hoje)
            datadas.append((titulo, rotulo, data))

        # 1. reconciliar: o quadro escreveu no `dia`?
        if rotulo in DIAS and DIAS.index(rotulo) != data.weekday():
            antes, data = data, data_do_dia(rotulo, hoje)
            arrastadas.append((titulo, antes, data))

        # 2. rolar o que ficou para trás
        if data < alvo:
            antes, data = data, alvo
            rolou += 1
            movidas.append((titulo, antes, rolou))
            if rolou >= EMPERRADA:
                emperradas.append((titulo, rolou))

        # 3. o rótulo é sempre derivado da data
        data = dia_util(data)
        novo = troca(troca(troca(cabeca, "data", data.isoformat()), "rolou", rolou),
                     "dia", DIAS[data.weekday()])
        if novo != cabeca:
            io.open(caminho, "w", encoding="utf-8").write("---" + novo + "---" + s.split("---", 2)[2])

    if not (movidas or arrastadas or datadas):
        return 0

    if datadas:
        print("Cartões novos do quadro, sem data — ganharam a do dia da coluna:")
        for titulo, rotulo, data in datadas:
            print(f"  {titulo} ({rotulo} → {data.isoformat()})")
        print()

    if arrastadas:
        print("Cartões arrastados — data reajustada ao dia da coluna:")
        for titulo, antes, depois in arrastadas:
            print("  %s (%s → %s)" % (titulo, antes.isoformat(), depois.isoformat()))
        print()
    if movidas:
        print("Rolagem de tarefas — %d movida(s) para %s:" % (len(movidas), alvo.isoformat()))
        for titulo, antes, rolou in movidas:
            print("  %s (de %s, %dª vez)" % (titulo, antes.isoformat(), rolou))
    if emperradas:
        print("\nEmperradas — rolaram %d+ vezes. Matar, quebrar em pedaços menores ou virar issue?" % EMPERRADA)
        for titulo, rolou in emperradas:
            print("  %s — %dx" % (titulo, rolou))
    return 0


if __name__ == "__main__":
    sys.exit(main())
