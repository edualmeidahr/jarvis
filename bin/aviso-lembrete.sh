#!/usr/bin/env bash
# O que um lembrete do Jarvis faz quando chega a hora: a bolha (fica no histórico) e a voz.
# Chamado pelos timers que o lembrar.py cria.
B="$HOME/.claude/bin"
python3 "$B/bolha.py" --nova --fim --som ouvindo "Lembrete" "$1"
python3 "$B/falar.py" "Lembrete: $1"
