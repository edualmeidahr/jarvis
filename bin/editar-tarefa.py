#!/usr/bin/env python3
"""Muda uma tarefa que já existe no quadro. Par do tarefa.sh, que só cria.

    editar-tarefa.py renomear "<tarefa>" "<nome novo>"
    editar-tarefa.py data     "<tarefa>" hoje|amanha|segunda..sexta|AAAA-MM-DD
    editar-tarefa.py concluir "<tarefa>"
    editar-tarefa.py reabrir  "<tarefa>"
    editar-tarefa.py issue    "<tarefa>" MLH037260|nenhuma
    editar-tarefa.py tag      "<tarefa>" codigo|vault|decisao|pedido|nenhuma
    editar-tarefa.py prazo    "<tarefa>" AAAA-MM-DD|nenhum
    editar-tarefa.py apagar   "<tarefa>"          vai para a lixeira (dá para desfazer)
    editar-tarefa.py listar   [busca]

<tarefa> não precisa ser o nome exato: vale um pedaço ("câmbio") ou o nome com erro de
transcrição. Se casar com mais de uma, não mexe em nada e lista as candidatas.

Apagar manda para a lixeira do sistema (`gio trash`), não some de vez: restaurar pelo
Arquivos → Lixeira. Para tarefa feita, o caminho de sempre é concluir (o /fechamento colhe).
`data` e `dia` mudam juntos — a rolagem desconfia quando eles discordam.
"""
import datetime as dt
import difflib
import glob
import os
import re
import subprocess
import sys
import unicodedata

VAULT = os.path.expanduser(os.environ.get("VAULT", "~/Documentos/obsidian"))
PASTAS = [os.path.join(VAULT, "07 Tarefas", "Notas"), os.path.join(VAULT, "07 Tarefas")]  # a raiz: cartões do + do quadro
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
TAGS = {"codigo", "vault", "decisao", "pedido"}


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", sem_acento.lower())).strip()


def tarefas():
    """{caminho: nome} das notas com `tipo: tarefa`."""
    achadas = {}
    for pasta in PASTAS:
        for f in glob.glob(os.path.join(pasta, "*.md")):
            try:
                cab = open(f, encoding="utf-8").read().split("---", 2)[1]
            except (OSError, IndexError):
                continue
            if re.search(r"^tipo:\s*tarefa\s*$", cab, re.M):
                achadas[f] = os.path.basename(f)[:-3]
    return achadas


def achar(busca):
    """(caminho, None) se achou uma; (None, [candidatas]) se nenhuma ou várias."""
    todas = tarefas()
    alvo = normalizar(busca)
    por_nome = {normalizar(n): c for c, n in todas.items()}
    if alvo in por_nome:
        return por_nome[alvo], None
    palavras = set(alvo.split())
    # um pedaço do nome, ou todas as palavras em qualquer ordem ("assumir nova issue")
    contem = [c for n, c in por_nome.items() if alvo and (alvo in n or palavras <= set(n.split()))]
    abertas = [c for c in contem if not re.search(r"^feito:\s*true", ler(c)[0], re.M)]
    if len(contem) > 1 and len(abertas) == 1:
        contem = abertas  # por voz, quem você quer mexer é quase sempre a que está em aberto
    if len(contem) == 1:
        return contem[0], None
    if not contem:
        perto = difflib.get_close_matches(alvo, por_nome, n=3, cutoff=0.6)
        if len(perto) == 1:
            return por_nome[perto[0]], None
        contem = [por_nome[p] for p in perto]
    return None, [todas[c] for c in contem]


# ---------------------------------------------------------------- frontmatter

def ler(caminho):
    texto = open(caminho, encoding="utf-8").read()
    _, cab, corpo = texto.split("---", 2)
    return cab, corpo


def gravar(caminho, cab, corpo):
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"---{cab}---{corpo}")
    os.replace(tmp, caminho)


def campo(cab, chave, valor):
    """Troca `chave: x` (e a lista que vier embaixo, no caso de tags); cria se faltar."""
    linha = f"{chave}: {valor}" if valor is not None else f"{chave}:"
    padrao = re.compile(rf"^{chave}:.*(?:\n[ \t]+- .*)*$", re.M)
    if padrao.search(cab):
        return padrao.sub(lambda _: linha, cab, count=1)
    return cab.rstrip("\n") + f"\n{linha}\n"


# ---------------------------------------------------------------- datas

def dia_util(d):
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def resolver_data(quando, hoje=None):
    hoje = hoje or dt.date.today()
    q = normalizar(quando)
    if q == "hoje":
        return dia_util(hoje)
    if q in ("amanha", "amanhã"):
        return dia_util(hoje + dt.timedelta(days=1))
    nomes = [normalizar(d) for d in DIAS[:5]]
    q = re.sub(r"\s*feira$", "", q)
    if q in nomes:  # "quinta": a desta semana, ou a da próxima se já passou
        alvo = nomes.index(q)
        return hoje + dt.timedelta(days=(alvo - hoje.weekday()) % 7)
    try:
        return dia_util(dt.date.fromisoformat(quando))
    except ValueError:
        return None


# ---------------------------------------------------------------- ações

def renomear(caminho, novo):
    novo = novo.strip().rstrip(".")
    if not novo:
        return None, "nome novo vazio"
    # as mesmas trocas do tarefa.sh: o Obsidian recusa estes caracteres em nome de nota
    arquivo = re.sub(r'[][\\/:*"<>|?#^]', "-", novo) + ".md"
    destino = os.path.join(os.path.dirname(caminho), arquivo)
    if os.path.exists(destino) and os.path.abspath(destino) != os.path.abspath(caminho):
        return None, f"já existe uma tarefa chamada {novo}"
    antigo = os.path.basename(caminho)[:-3]
    cab, corpo = ler(caminho)
    corpo = re.sub(rf"^# {re.escape(antigo)}\s*$", f"# {novo}", corpo, count=1, flags=re.M)
    if re.search(r"^titulo:", cab, re.M):
        cab = campo(cab, "titulo", novo)
    gravar(caminho, cab, corpo)
    os.rename(caminho, destino)
    return destino, f"renomeada: {antigo} → {novo}"


def mudar_data(caminho, quando):
    d = resolver_data(quando)
    if not d:
        return None, f"não entendi a data: {quando}"
    cab, corpo = ler(caminho)
    cab = campo(campo(cab, "data", d.isoformat()), "dia", DIAS[d.weekday()])
    gravar(caminho, cab, corpo)
    return caminho, f"{os.path.basename(caminho)[:-3]} → {DIAS[d.weekday()]}, {d:%d/%m}"


def simples(chave, valor, frase):
    def fazer(caminho, *_):
        cab, corpo = ler(caminho)
        gravar(caminho, campo(cab, chave, valor), corpo)
        return caminho, f"{os.path.basename(caminho)[:-3]}: {frase}"
    return fazer


def mudar_issue(caminho, issue):
    issue = issue.strip().upper()
    if normalizar(issue) in ("nenhuma", "nenhum", ""):
        return simples("issue", None, "sem issue")(caminho)
    if not re.fullmatch(r"[A-Z]{2,4}\d{5,6}", issue):
        return None, f"issue com formato estranho: {issue}"
    return simples("issue", issue, f"issue {issue}")(caminho)


def mudar_tag(caminho, tag):
    tag = normalizar(tag)
    if tag in ("nenhuma", "nenhum", ""):
        return simples("tags", None, "sem tag")(caminho)
    if tag not in TAGS:
        return None, f"tag desconhecida: {tag} (vale {', '.join(sorted(TAGS))})"
    cab, corpo = ler(caminho)
    cab = campo(cab, "tags", f"\n  - {tag}").replace("tags: \n", "tags:\n")
    gravar(caminho, cab, corpo)
    return caminho, f"{os.path.basename(caminho)[:-3]}: tag {tag}"


def mudar_prazo(caminho, quando):
    if normalizar(quando) in ("nenhum", "nenhuma", ""):
        return simples("prazo", None, "sem prazo")(caminho)
    d = resolver_data(quando)
    if not d:
        return None, f"não entendi o prazo: {quando}"
    return simples("prazo", d.isoformat(), f"prazo {d:%d/%m}")(caminho)


def apagar(caminho, *_):
    r = subprocess.run(["gio", "trash", caminho], capture_output=True, text=True)
    if r.returncode != 0:  # sem lixeira, não apaga: rm de verdade não tem volta
        return None, f"não consegui mandar para a lixeira: {r.stderr.strip()}"
    return caminho, f"{os.path.basename(caminho)[:-3]}: na lixeira"


ACOES = {
    "apagar": (apagar, 0),
    "renomear": (renomear, 1),
    "data": (mudar_data, 1),
    "concluir": (simples("feito", "true", "concluída"), 0),
    "reabrir": (simples("feito", "false", "reaberta"), 0),
    "issue": (mudar_issue, 1),
    "tag": (mudar_tag, 1),
    "prazo": (mudar_prazo, 1),
}


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 1
    acao = args[0]
    if acao == "listar":
        busca = normalizar(" ".join(args[1:]))
        for caminho, nome in sorted(tarefas().items(), key=lambda x: x[1]):
            cab, _ = ler(caminho)
            g = lambda k: (re.search(rf"^{k}:[ \t]*(.*)$", cab, re.M) or [None, ""])[1].strip()
            if not busca or busca in normalizar(nome):
                print(f"{'✓' if g('feito') == 'true' else '☐'} {nome} — {g('dia')} {g('data')}")
        return 0
    if acao not in ACOES or len(args) < 2 + ACOES[acao][1]:
        print(__doc__, file=sys.stderr)
        return 1
    caminho, candidatas = achar(args[1])
    if not caminho:
        if candidatas:
            print("mais de uma tarefa casa, diga qual:\n  " + "\n  ".join(candidatas))
        else:
            print(f"nenhuma tarefa parecida com: {args[1]}")
        return 1
    fazer, n = ACOES[acao]
    feito, frase = fazer(caminho, *args[2:2 + n])
    print(frase)
    return 0 if feito else 1


if __name__ == "__main__":
    sys.exit(main())
