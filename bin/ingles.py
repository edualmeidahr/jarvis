#!/usr/bin/env python3
"""Inglês com sotaque brasileiro na voz do Jarvis: "issue", "threads", "Highway to Hell".

A voz (Piper pt_BR faber) lê tudo com as regras do português: "issue" saía "issu-e", "threads"
saía "tre-ads". Aqui os trechos em inglês viram fonemas do inglês (espeak-ng en-us), trocados
pelo som brasileiro mais próximo — a voz só foi treinada com os sons do português, e um som
de fora (θ, ɹ, ʌ) sai estranho. O resultado é inglês falado por brasileiro: "isshu", "tréds".

Quem é inglês:
- palavra na lista ~/.config/jarvis/ingles.txt (marcas que a frequência não pega: Spotify);
- palavra bem mais comum em inglês que em português (wordfreq) e rara em português:
  "issue", "deploy", "highway"; "time", "come" e "me" ficam em português;
- palavra comum nas duas ("to", "me", "of", "the") vira inglês quando encosta em outra inglesa:
  "Wake Me Up", "Fear of the Dark".
O dicionário de pronúncia (pronuncia.txt) vem antes e ganha: o que você ajustou à mão fica.

Roda no venv do Jarvis (wordfreq e piper-tts), dentro do sintese.py.

    ingles.py "frase"     mostra o que viraria inglês e os fonemas
"""
import os
import re
import sys
import unicodedata

from wordfreq import zipf_frequency

LISTA = os.path.expanduser("~/.config/jarvis/ingles.txt")

# frequência (escala zipf: 7 = "the", 3 = rara). Inglesa: bem mais comum em inglês e incomum em português
DIFERENCA = 0.8
MAX_PT = 4.0
MIN_EN = 3.0

_fon = None


def _fonemizador():
    global _fon
    if _fon is None:
        import piper
        from piper.phonemize_espeak import EspeakPhonemizer
        _fon = EspeakPhonemizer(os.path.join(os.path.dirname(piper.__file__), "espeak-ng-data"))
    return _fon


def _lista():
    try:
        linhas = open(LISTA).read().splitlines()
    except OSError:
        return set()
    return {l.strip().lower() for l in linhas if l.strip() and not l.startswith("#")}


def _tem_acento(p):
    return any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", p)) or "ç" in p.lower()


def _inglesa(p, lista):
    w = p.lower()
    if w in lista:
        return True
    if _tem_acento(w) or len(w) < 2:
        return False
    en, pt = zipf_frequency(w, "en"), zipf_frequency(w, "pt")
    return en >= MIN_EN and pt < MAX_PT and en - pt >= DIFERENCA


def _vizinha(p):
    """Comum nas duas línguas, e não bem mais portuguesa: "to", "me", "of", "the", "up".
    De uma letra nunca: "a issue" segue com o artigo em português."""
    w = p.lower()
    return len(w) >= 2 and not _tem_acento(w) and zipf_frequency(w, "en") >= zipf_frequency(w, "pt") - 0.2


# do inglês (espeak en-us) para os sons que a voz brasileira conhece. Ordem importa: ditongos e
# sons de dois símbolos primeiro
TROCAS = [
    ("oʊ", "ow"), ("eɪ", "ej"), ("aɪ", "aj"), ("ɔɪ", "ɔj"), ("aʊ", "aw"),
    ("ɜːɾ", "ɛɾ"), ("ɜː", "ɛɾ"), ("ɜ", "ɛ"), ("ɚ", "eɾ"), ("ɑːɾ", "aɾ"), ("ɔːɾ", "ɔɾ"),  # o ɹ já virou ɾ
    # o "o" americano ("Spotify", "rock", "job"): brasileiro fala "ó"
    ("ɑː", "ɔ"), ("ɑ", "ɔ"), ("æ", "ɛ"), ("ʌ", "a"), ("ɒ", "ɔ"), ("ᵻ", "i"), ("ɪ", "i"), ("ʊ", "u"),
    ("iː", "i"), ("uː", "u"), ("ɔː", "ɔ"), ("ː", ""),
    ("θ", "t"), ("ð", "d"), ("ʔ", ""), ("ʲ", ""), ("ɐ", "a"),
]


def _abrasileirar(fonemas):
    # o "t" americano entre vogais ("Spotify" = spɑːɾᵻfaɪ) vira "t" de novo: antes de mexer no "r"
    fonemas = fonemas.replace("ɾ", "t")
    # "r" do começo da palavra soa como o "r" forte do português ("review" = "riviu"); o resto, brando
    fonemas = re.sub(r"(^|\s|ˈ|ˌ)ɹ", r"\1x", fonemas)
    fonemas = fonemas.replace("ɹ", "ɾ")
    # "h" do inglês: o mais perto que a voz tem é o "rr" ("hell" = "rrél")
    fonemas = re.sub(r"(^|\s|ˈ|ˌ)h", r"\1x", fonemas).replace("h", "")
    for de, para in TROCAS:
        fonemas = fonemas.replace(de, para)
    return fonemas


def fonemas_ingles(trecho):
    return _abrasileirar(" ".join("".join(f) for f in _fonemizador().phonemize("en-us", trecho)).strip())


PALAVRA = re.compile(r"[A-Za-zÀ-ÿ']+")


def trechos(texto):
    """[(início, fim)] dos trechos em inglês do texto."""
    lista = _lista()
    palavras = list(PALAVRA.finditer(texto))
    ingl = [_inglesa(m.group(), lista) for m in palavras]
    # vizinhas: "to" entre "Highway" e "Hell", "me" e "up" depois de "Wake"
    mudou = True
    while mudou:
        mudou = False
        for i, m in enumerate(palavras):
            if ingl[i] or not _vizinha(m.group()):
                continue
            perto = [j for j in (i - 1, i + 1) if 0 <= j < len(palavras) and ingl[j]
                     and not re.search(r"[.,;:!?]", texto[min(m.end(), palavras[j].end()):max(m.start(), palavras[j].start())])]
            if perto:
                ingl[i], mudou = True, True
    # junta palavras inglesas seguidas (separadas só por espaço) num trecho: a pronúncia emenda melhor
    saida = []
    for i, m in enumerate(palavras):
        if not ingl[i]:
            continue
        if saida and texto[saida[-1][1]:m.start()].strip() == "":
            saida[-1] = (saida[-1][0], m.end())
        else:
            saida.append((m.start(), m.end()))
    return saida


def marcar(texto):
    """O texto com os trechos em inglês trocados por [[ fonemas ]] (sintaxe do piper-tts)."""
    partes, fim = [], 0
    for a, b in trechos(texto):
        partes.append(texto[fim:a])
        partes.append(f"[[ {fonemas_ingles(texto[a:b])} ]]")
        fim = b
    partes.append(texto[fim:])
    return "".join(partes)


if __name__ == "__main__":
    frase = " ".join(sys.argv[1:])
    for a, b in trechos(frase):
        print(f"inglês: {frase[a:b]!r} → {fonemas_ingles(frase[a:b])}")
    print(marcar(frase))
