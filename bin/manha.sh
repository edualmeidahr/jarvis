#!/usr/bin/env bash
# Rotina das 7h: rola as tarefas, busca o GitLab e a agenda, põe as reuniões
# no quadro, redesenha o painel, põe a foto do GitLab na diária, avisa.
# Chamado pelo timer do systemd (sydle-manha.timer). Rodar na mão também serve:
# é assim que se atualiza o Painel.md no meio do dia.
#
# A ordem importa:
#   1. rolagem primeiro e sempre — o quadro não pode depender da rede
#   2. GitLab e agenda podem falhar; o resto sai mesmo assim, com o último dado bom
#   3. a notificação sai uma vez por dia, só quando o GitLab respondeu
# Sai com 1 se GitLab ou agenda falharam por rede, para o systemd tentar de novo.
set -uo pipefail

BIN="$HOME/.claude/bin"
CACHE="$HOME/.claude/cache"
PATH="$HOME/.local/bin:$PATH"

python3 "$BIN/rolagem.py"

python3 "$BIN/gitlab.py"
gitlab=$?
# notas das suas threads na pasta de cada MR; antes do painel, para a diária linkar
python3 "$BIN/notas_mr.py"

python3 "$BIN/agenda.py"
agenda=$?
python3 "$BIN/reunioes.py"

python3 "$BIN/painel.py"
python3 "$BIN/painel.py" --diaria

carimbo="$CACHE/notificado-$(date +%F)"
if [ "$gitlab" -eq 0 ] && [ ! -e "$carimbo" ]; then
  python3 "$BIN/painel.py" --notificar && touch "$carimbo"
  # sem voz aqui de propósito: às 7h você pode não estar na frente do computador.
  # O bom dia falado sai quando você diz "bom dia" no Insert (jarvis.py).
  find "$CACHE" -maxdepth 1 -name 'notificado-*' ! -name "notificado-$(date +%F)" -delete 2>/dev/null
fi

[ "$gitlab" -ne 0 ] || [ "$agenda" -ne 0 ] && exit 1
exit 0
