#!/usr/bin/env bash
# Cria uma tarefa em 07 Tarefas/Notas/ sem abrir o Obsidian.
#
#   tarefa.sh "texto"                                  → hoje, sem tag
#   tarefa.sh "texto" amanha                           → próximo dia útil
#   tarefa.sh "texto" 2026-09-25 MLH037260 codigo      → data, issue e tag
#   tarefa.sh -p Danilo -d 2026-09-29 "texto" hoje "" pedido
#                                                      → pedido de alguém, com prazo
#
# tag pinta o cartão no quadro: codigo · vault · decisao · pedido
#
# `data` é quando você vai fazer (a coluna). `prazo` é quando precisa estar
# pronto. São coisas diferentes: um pedido pode entrar hoje com prazo na terça.
set -euo pipefail

VAULT="${VAULT:-$HOME/Documentos/obsidian}"
PASTA="$VAULT/07 Tarefas/Notas"

PEDIDO=""; PRAZO=""
while getopts "p:d:" op; do
  case "$op" in
    p) PEDIDO="$OPTARG" ;;
    d) PRAZO="$OPTARG" ;;
    *) exit 1 ;;
  esac
done
shift $((OPTIND - 1))

[ $# -ge 1 ] || { echo "uso: tarefa.sh [-p quem] [-d AAAA-MM-DD] \"texto\" [hoje|amanha|AAAA-MM-DD] [ISSUE] [TAG]" >&2; exit 1; }

TEXTO="$1"; QUANDO="${2:-hoje}"; ISSUE="${3:-}"; TAG="${4:-}"

case "$QUANDO" in
  hoje)   BASE=0 ;;
  amanha) BASE=1 ;;
  *)      BASE="" ;;
esac

# a data final é sempre dia útil, e o rótulo do dia sai dela
leitura=$(python3 - "$QUANDO" "$BASE" <<'PY'
import sys, datetime as d
DIAS = ["segunda","terça","quarta","quinta","sexta"]
quando, base = sys.argv[1], sys.argv[2]
if base == "":
    x = d.date.fromisoformat(quando)
else:
    x = d.date.today() + d.timedelta(days=int(base))
while x.weekday() >= 5:
    x += d.timedelta(days=1)
print(x.isoformat(), DIAS[x.weekday()])
PY
)
DATA="${leitura% *}"; DIA="${leitura#* }"

# o Obsidian recusa estes caracteres em nome de nota: | é o apelido do [[link|x]],
# # e ^ apontam para seção e bloco, [ ] fecham o link, / é pasta
ARQ="$PASTA/$(printf '%s' "$TEXTO" | sed -E 's/[][\\/:*"<>|?#^]/-/g').md"
[ -e "$ARQ" ] && { echo "já existe: $ARQ" >&2; exit 1; }

mkdir -p "$PASTA"
{
  echo "---"
  echo "tipo: tarefa"
  echo "data: $DATA"
  echo "dia: $DIA"
  echo "feito: false"
  echo "rolou: 0"
  echo "issue: $ISSUE"
  [ -n "$PEDIDO" ] && echo "pedido_por: $PEDIDO"
  [ -n "$PRAZO" ] && echo "prazo: $PRAZO"
  if [ -n "$TAG" ]; then printf 'tags:\n  - %s\n' "$TAG"; else echo "tags:"; fi
  echo "---"
  echo
  echo "# $TEXTO"
} > "$ARQ"
echo "$DATA ($DIA) — $TEXTO"
