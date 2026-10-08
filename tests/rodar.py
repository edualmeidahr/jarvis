#!/usr/bin/env python3
"""Teste das frases: cada frase falada tem que virar o comando certo.

    tests/rodar.py                 confere tests/frases.tsv; sai com 1 se alguma falhar
    tests/rodar.py -v              mostra também as que passaram
    tests/rodar.py --ver "frase"   só mostra o que a frase vira hoje
    tests/rodar.py --gerar arq     rascunho: cada linha do arquivo com o resultado de hoje

frases.tsv: "frase<TAB>esperado", uma por linha; # comenta. O esperado é o resumo que a
função resumo() abaixo devolve, por exemplo:
    acao midia pausar · acao tocar bruno e marrone · acao volume 7 · comando proxima
    lembrete em 1200 · memorizar · traduzir ingles · encerrar · tarefa concluir · claude · ditado

Rode antes de cada mudança no reconhecimento: quase toda correção já quebrou outra frase.
Não fala, não toca, não chama o Claude: só o reconhecimento (jarvis.reconhecer).
"""
import importlib.util
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(AQUI), "bin")
FRASES = os.path.join(AQUI, "frases.tsv")

_spec = importlib.util.spec_from_file_location("jarvis", os.path.join(BIN, "jarvis.py"))
jarvis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jarvis)
# o teste não pode depender do que aconteceu agora há pouco no uso real
jarvis.INTERROMPIDO = os.path.join(AQUI, "nao-existe")


def resumo(frase):
    tipo, valor = jarvis.reconhecer(frase)
    if tipo == "acao":
        nome, arg = valor
        return f"acao {nome} {jarvis.normalizar(str(arg))}".strip()
    if tipo == "comando":
        return f"comando {valor}"
    if tipo == "tarefa":
        return f"tarefa {valor[0]}"
    if tipo == "lembrete":
        return f"lembrete {valor[0]} {valor[1]}"
    if tipo == "traduzir":
        return f"traduzir {valor or 'auto'}"
    if tipo in ("claude", "ditado", "memorizar", "esquecer", "encerrar"):
        return tipo
    return f"{tipo} {valor}"


def ler():
    casos = []
    for n, linha in enumerate(open(FRASES, encoding="utf-8"), 1):
        linha = linha.rstrip("\n")
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        frase, _, esperado = linha.partition("\t")
        casos.append((n, frase, re.sub(r"\s+", " ", esperado.strip())))
    return casos


def main():
    args = sys.argv[1:]
    if args[:1] == ["--ver"]:
        print(resumo(" ".join(args[1:])))
        return 0
    if args[:1] == ["--gerar"]:
        for frase in open(args[1], encoding="utf-8"):
            frase = frase.strip()
            if frase:
                print(f"{frase}\t{resumo(frase)}")
        return 0
    verboso = "-v" in args
    falhas = 0
    casos = ler()
    for n, frase, esperado in casos:
        obtido = resumo(frase)
        if obtido != esperado:
            falhas += 1
            print(f"✗ linha {n}: {frase!r}\n    esperado: {esperado}\n    veio:     {obtido}")
        elif verboso:
            print(f"✓ {frase!r} → {obtido}")
    print(f"\n{len(casos) - falhas}/{len(casos)} frases certas" + (f", {falhas} falhando" if falhas else ""))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
