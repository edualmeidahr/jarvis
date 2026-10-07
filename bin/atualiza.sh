#!/usr/bin/env bash
# Roda o manha.sh na hora e avisa como foi. É a porta de entrada manual:
#   manha            no terminal (link em ~/.local/bin)
#   Ctrl+Alt+M       atalho do GNOME
#   "atualiza o gitlab" no Insert (jarvis.py)
# O manha.sh só notifica uma vez por dia; aqui a notificação sai sempre,
# porque quem chamou na mão quer saber se deu certo.
set -uo pipefail

BIN="$HOME/.claude/bin"
CACHE="$HOME/.claude/cache"
PATH="$HOME/.local/bin:$PATH"

# não roda duas vezes ao mesmo tempo (atalho apertado duas vezes, ou o timer das 7h)
exec 9>"$CACHE/manha.lock"
if ! flock -n 9; then
  python3 "$BIN/bolha.py" --fim "Já estou atualizando o GitLab" "Só um instante."
  exit 2
fi

"$BIN/manha.sh"
status=$?

# pela voz, quem fecha a bolha é o Jarvis, falando; aqui seria uma segunda notificação
[ -n "${JARVIS_SEM_BOLHA:-}" ] && exit "$status"

erro=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("erro") or "")' "$CACHE/gitlab.json" 2>/dev/null)
if [ -n "$erro" ]; then
  python3 "$BIN/bolha.py" --fim "Não consegui falar com o GitLab" "Fiquei com o último dado bom. ($erro)"
else
  corpo=$(python3 "$BIN/painel.py" --resumo | sed 's/^GitLab — //')
  python3 "$BIN/bolha.py" --fim "Atualizei o painel" "${corpo:-Nada pendente no GitLab.}"
fi
exit "$status"
