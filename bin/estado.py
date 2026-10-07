#!/usr/bin/env python3
"""O que o Jarvis está fazendo agora, para o indicador na barra do GNOME.

    estado.py ouvindo|pensando|falando|parado ["detalhe"]

Também importável: estado.marcar("falando", "Lembrete: café").
Grava $XDG_RUNTIME_DIR/jarvis/estado.json de forma atômica: a extensão lê a cada meio
segundo e nunca pega o arquivo pela metade. Estado velho (processo que morreu no meio)
a extensão trata como parado, pelo campo "validade".
"""
import json
import os
import sys
import time

ARQUIVO = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis", "estado.json")
# quanto tempo cada estado vale sem ser renovado; depois disso, a barra volta ao cinza
VALIDADE_S = {"ouvindo": 30, "pensando": 120, "falando": 120, "parado": 0}


def marcar(estado, detalhe=""):
    if estado not in VALIDADE_S:
        return
    os.makedirs(os.path.dirname(ARQUIVO), exist_ok=True)
    tmp = f"{ARQUIVO}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump({"estado": estado, "detalhe": detalhe[:120], "desde": time.time(),
                   "validade": VALIDADE_S[estado]}, f, ensure_ascii=False)
    os.replace(tmp, ARQUIVO)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in VALIDADE_S:
        sys.exit(__doc__)
    marcar(sys.argv[1], " ".join(sys.argv[2:]))
