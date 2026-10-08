#!/usr/bin/env bash
# Liga e desliga o "Ei Jarvis" (serviço jarvis-escuta). Atalho: Ctrl+Alt+J e o botão do painel.
# Desligado, o microfone só abre quando você aperta o Insert.
#
# A escolha fica num arquivo de marca, que o serviço confere ao subir (ConditionPathExists):
# desligado continua desligado depois do login. NÃO usar "systemctl disable" aqui — com o
# serviço instalado como link para o repositório, o disable apagava o próprio arquivo, e o
# botão não conseguia mais religar.
B="$HOME/.claude/bin/bolha.py"
MARCA="$HOME/.config/jarvis/escuta-desligada"
if systemctl --user is-active --quiet jarvis-escuta; then
  mkdir -p "$(dirname "$MARCA")" && touch "$MARCA"
  systemctl --user stop jarvis-escuta
  python3 "$B" --nova --fim --importante "Parei de ouvir" "Agora só pelo Insert. Ctrl+Alt+J liga de novo."
else
  rm -f "$MARCA"
  if systemctl --user start jarvis-escuta; then
    python3 "$B" --nova --fim --importante "Estou ouvindo" "É só dizer “Ei Jarvis”. Ctrl+Alt+J desliga."
  else
    python3 "$B" --nova --fim --importante "Não consegui ligar a escuta" "Veja: systemctl --user status jarvis-escuta"
  fi
fi
