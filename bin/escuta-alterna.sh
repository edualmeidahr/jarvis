#!/usr/bin/env bash
# Liga e desliga o "Ei Jarvis" (serviço jarvis-escuta). Atalho: Ctrl+Alt+J.
# Desligado, o microfone só abre quando você aperta o Insert.
B="$HOME/.claude/bin/bolha.py"
if systemctl --user is-active --quiet jarvis-escuta; then
  systemctl --user stop jarvis-escuta
  systemctl --user disable --quiet jarvis-escuta  # desligado continua desligado depois do login
  python3 "$B" --nova --fim "Parei de ouvir" "Agora só pelo Insert. Ctrl+Alt+J liga de novo."
else
  systemctl --user enable --now --quiet jarvis-escuta
  python3 "$B" --nova --fim "Estou ouvindo" "É só dizer “Ei Jarvis”. Ctrl+Alt+J desliga."
fi
