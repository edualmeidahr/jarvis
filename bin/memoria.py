#!/usr/bin/env python3
"""O que você mandou o Jarvis lembrar para sempre ("lembra que...").

    memoria.py guardar "a senha do Wi-Fi da sala é X"
    memoria.py listar
    memoria.py esquecer "wi-fi"      apaga as linhas que contêm o trecho (sem acento, sem caixa)

Fica numa nota do vault, 00 Inbox/Jarvis - memória.md: dá para ler e editar no Obsidian.
Uma linha por fato, com a data. O jarvis.py põe esta lista no prompt do Claude.
"""
import datetime as dt
import os
import re
import sys
import unicodedata

NOTA = os.path.expanduser("~/Documentos/obsidian/00 Inbox/Jarvis - memória.md")
CABECALHO = """---
tipo: memoria-jarvis
---

# Jarvis — memória

O que você pediu para o Jarvis lembrar. Pode editar à mão: uma linha por fato.

"""


def normalizar(t):
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()


def fatos():
    try:
        linhas = open(NOTA, encoding="utf-8").read().splitlines()
    except OSError:
        return []
    return [l[2:].strip() for l in linhas if l.startswith("- ")]


def guardar(fato):
    fato = " ".join(fato.split()).rstrip(".")
    if not fato:
        return None
    fato = fato[0].upper() + fato[1:]
    if not os.path.exists(NOTA):
        os.makedirs(os.path.dirname(NOTA), exist_ok=True)
        open(NOTA, "w", encoding="utf-8").write(CABECALHO)
    with open(NOTA, "a", encoding="utf-8") as f:
        f.write(f"- {fato} ({dt.date.today():%d/%m/%Y})\n")
    return fato


def esquecer(trecho):
    alvo = normalizar(trecho).strip()
    if not alvo or not os.path.exists(NOTA):
        return []
    linhas = open(NOTA, encoding="utf-8").read().splitlines(keepends=True)
    fora = [l for l in linhas if l.startswith("- ") and alvo in normalizar(l)]
    open(NOTA, "w", encoding="utf-8").writelines(l for l in linhas if l not in fora)
    return [re.sub(r"^- |\s*\(\d\d/\d\d/\d{4}\)\s*$", "", l) for l in fora]


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    if args[0] == "listar":
        print("\n".join(fatos()) or "(nada guardado)")
    elif args[0] == "guardar" and len(args) > 1:
        print(f"guardado: {guardar(' '.join(args[1:]))}")
    elif args[0] == "esquecer" and len(args) > 1:
        fora = esquecer(" ".join(args[1:]))
        print("esquecido: " + "; ".join(fora) if fora else "nada parecido para esquecer")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
