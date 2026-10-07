#!/usr/bin/env bash
# Captura rápida: chegou um pedido, aperta Ctrl+Alt+N, responde três perguntas.
# A tarefa cai em hoje no quadro, com tag `pedido`. Arraste para outro dia se for o caso.
#
# O atalho do GNOME roda com PATH enxuto, por isso os caminhos são absolutos.
set -uo pipefail

TAREFA="$HOME/.claude/bin/tarefa.sh"
SEP=$'\x1f'   # separador que ninguém digita; "|" quebraria com texto livre

resposta=$(zenity --forms --title="Novo pedido" --text="Vai para hoje no quadro." \
  --separator="$SEP" \
  --add-entry="O que é" \
  --add-entry="Quem pediu (opcional)" \
  --add-entry="Prazo DD/MM ou DD/MM/AAAA (opcional)" 2>/dev/null) || exit 0   # cancelou

IFS="$SEP" read -r texto quem prazo_bruto <<<"$resposta"
texto=$(printf '%s' "$texto" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')
[ -n "$texto" ] || { zenity --error --text="Faltou dizer o que é." 2>/dev/null; exit 1; }

prazo=""
if [ -n "${prazo_bruto// /}" ]; then
  # DD/MM sem ano que já passou este ano é do ano que vem
  prazo=$(python3 - "$prazo_bruto" <<'PY'
import sys, datetime as d
t = sys.argv[1].strip()
hoje = d.date.today()
try:
    if t.count("/") == 1:
        x = d.datetime.strptime(f"{t}/{hoje.year}", "%d/%m/%Y").date()
        if x < hoje:
            x = x.replace(year=hoje.year + 1)
    else:
        x = d.datetime.strptime(t, "%d/%m/%Y").date()
    print(x.isoformat())
except ValueError:
    sys.exit(1)
PY
) || { zenity --error --text="Prazo inválido: $prazo_bruto\nUse 29/09 ou 29/09/2026." 2>/dev/null; exit 1; }
fi

args=()
[ -n "$quem" ] && args+=(-p "$quem")
[ -n "$prazo" ] && args+=(-d "$prazo")

if saida=$("$TAREFA" "${args[@]}" "$texto" hoje "" pedido 2>&1); then
  notify-send -a "Jarvis" "Pedido anotado" "$saida${prazo:+ · prazo $prazo}" 2>/dev/null
else
  zenity --error --text="Não criei a tarefa:\n$saida" 2>/dev/null
  exit 1
fi
