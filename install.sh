#!/usr/bin/env bash
# Monta o Jarvis nesta máquina a partir do repositório. Pode rodar de novo à vontade.
#
#   ./install.sh            tudo: links, Python, voz, Whisper, atalhos e serviços
#   ./install.sh links      só os links (os lugares de sempre passam a apontar para cá)
#   ./install.sh trabalho   também liga os timers do GitLab e da agenda (precisa do local.env)
#   ./install.sh spotify    o player do Spotify (spotifyd) e o serviço dele (precisa de Premium)
#
# Nada aqui usa sudo. O que precisa de pacote do sistema fica listado no fim, para você.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
DADOS="$HOME/.local/share/jarvis"
PIPER_DIR="$HOME/.local/share/piper"
WHISPER_DIR="$HOME/.local/share/whisper.cpp"
MODELOS="$HOME/.local/share/whisper-models"
GNOME_EXT="jarvis@eduardo.almeida"
PIPER_VERSAO="2023.11.14-2"
SPOTIFYD_VERSAO="v0.4.2"
VOZ="pt_BR-faber-medium"
FALTA=()

passo() { printf '\n== %s\n' "$1"; }

# liga o caminho de sempre ao arquivo do repositório; o que já existia vai para .antes-repo
ligar() {
  local origem="$1" destino="$2"
  mkdir -p "$(dirname "$destino")"
  if [ -L "$destino" ]; then
    rm "$destino"
  elif [ -e "$destino" ]; then
    mv "$destino" "$destino.antes-repo-$(date +%Y%m%d%H%M%S)"
  fi
  ln -s "$origem" "$destino"
}

links() {
  passo "Links"
  ligar "$REPO/bin" "$HOME/.claude/bin"
  ligar "$REPO/gnome/$GNOME_EXT" "$HOME/.local/share/gnome-shell/extensions/$GNOME_EXT"
  ligar "$REPO/chrome" "$DADOS/extensao"
  for f in "$REPO"/config/*.txt "$REPO"/config/menu.json; do
    ligar "$f" "$HOME/.config/jarvis/$(basename "$f")"
  done
  ligar "$REPO/config/pipewire" "$HOME/.config/jarvis/pipewire"
  ligar "$REPO/config/spotifyd" "$HOME/.config/jarvis/spotifyd"
  # serviços são COPIADOS, não linkados: "systemctl disable" num serviço que é link apaga o
  # próprio arquivo (o botão do painel quebrou assim). Mudou algo em systemd/? Rode de novo.
  for f in "$REPO"/systemd/*; do
    local destino="$HOME/.config/systemd/user/$(basename "$f")"
    [ -L "$destino" ] && rm "$destino"
    install -m 644 "$f" "$destino"
  done
  [ -e "$HOME/.config/jarvis/local.env" ] || FALTA+=("criar ~/.config/jarvis/local.env a partir de config/local.env.exemplo (só para o GitLab)")

  # ponte da extensão do Chrome: o ID vem da chave pública do manifest, igual em toda máquina
  local id
  id=$(cat "$REPO/chrome/.id")
  mkdir -p "$HOME/.config/google-chrome/NativeMessagingHosts"
  cat > "$HOME/.config/google-chrome/NativeMessagingHosts/com.jarvis.musica.json" <<EOF
{
  "name": "com.jarvis.musica",
  "description": "Ponte do Jarvis com a extensão Jarvis Música",
  "path": "$HOME/.claude/bin/musica.py",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$id/"]
}
EOF
  chmod +x "$REPO"/bin/*.py "$REPO"/bin/*.sh "$REPO"/tests/*.py "$REPO"/tests/pre-commit
  # o git roda o teste das frases antes de cada commit
  [ -d "$REPO/.git" ] && ln -sf ../../tests/pre-commit "$REPO/.git/hooks/pre-commit"
  systemctl --user daemon-reload
}

python_venv() {
  passo "Python do Jarvis (escuta e timbre)"
  if [ ! -x "$DADOS/venv/bin/python" ]; then
    if command -v uv >/dev/null; then
      uv venv --python 3.11 "$DADOS/venv"
    else
      python3 -m venv "$DADOS/venv"
    fi
  fi
  local pip=(uv pip install --python "$DADOS/venv/bin/python")
  command -v uv >/dev/null || pip=("$DADOS/venv/bin/pip" install)
  # openwakeword sem dependências: ele puxa o tflite-runtime, que não existe para esta versão
  "${pip[@]}" -q openwakeword --no-deps
  "${pip[@]}" -q onnxruntime numpy scipy tqdm requests scikit-learn "setuptools<81"
  "$DADOS/venv/bin/python" -c "from openwakeword.utils import download_models; download_models(['hey_jarvis'])" >/dev/null
  # reconhecedor do "Ei Jarvis": Parakeet v3 pelo sherpa-onnx (0,4-1 s por pedido; o Whisper levava 3-4 s)
  "${pip[@]}" -q sherpa-onnx
  local stt="$DADOS/stt/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
  if [ ! -f "$stt/encoder.int8.onnx" ]; then
    mkdir -p "$DADOS/stt"
    curl -fsSL "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2" \
      | tar xj -C "$DADOS/stt"
  fi
}

voz() {
  passo "Voz (Piper + $VOZ)"
  if [ ! -x "$PIPER_DIR/piper/piper" ]; then
    mkdir -p "$PIPER_DIR"
    curl -sSL "https://github.com/rhasspy/piper/releases/download/$PIPER_VERSAO/piper_linux_x86_64.tar.gz" | tar xz -C "$PIPER_DIR"
  fi
  mkdir -p "$PIPER_DIR/vozes"
  local base="https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium"
  for ext in onnx onnx.json; do
    [ -f "$PIPER_DIR/vozes/$VOZ.$ext" ] || curl -sSL -o "$PIPER_DIR/vozes/$VOZ.$ext" "$base/$VOZ.$ext"
  done
}

whisper() {
  passo "Whisper (transcrição local)"
  mkdir -p "$MODELOS"
  [ -f "$MODELOS/ggml-small.bin" ] || curl -sSL -o "$MODELOS/ggml-small.bin" \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
  if [ -x "$WHISPER_DIR/build/bin/whisper-cli" ]; then
    return
  fi
  if command -v cmake >/dev/null && command -v g++ >/dev/null; then
    [ -d "$WHISPER_DIR" ] || git clone -q --depth 1 https://github.com/ggerganov/whisper.cpp "$WHISPER_DIR"
    cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build "$WHISPER_DIR/build" -j "$(nproc)" --target whisper-cli >/dev/null
  else
    FALTA+=("compilar o whisper.cpp: precisa de cmake e g++ (sudo apt install cmake g++), depois rode ./install.sh de novo")
  fi
}

atalho() {  # nome-curto, título, tecla, comando
  local base=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
  local k="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$base/$1/"
  local lista
  lista=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)
  lista=$(python3 -c "import ast,sys; l=ast.literal_eval(sys.argv[1].replace('@as ','')); p=sys.argv[2]; l.append(p) if p not in l else None; print(l)" "$lista" "$base/$1/")
  gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$lista"
  gsettings set "$k" name "$2"
  gsettings set "$k" binding "$3"
  gsettings set "$k" command "$4"
}

gnome() {
  passo "Atalhos e extensão do GNOME"
  atalho voz "Ditado — gravar/transcrever" "Insert" "$HOME/.claude/bin/voz.sh"
  atalho voz-cancela "Ditado — cancelar" "<Control><Alt>x" "$HOME/.claude/bin/voz.sh cancela"
  atalho captura "Captura rápida de pedido" "<Control><Alt>n" "$HOME/.claude/bin/captura.sh"
  atalho manha "Atualizar GitLab (manha.sh)" "<Control><Alt>m" "$HOME/.claude/bin/atualiza.sh"
  atalho jarvis-escuta "Jarvis — ligar/desligar \"Ei Jarvis\"" "<Control><Alt>j" "$HOME/.claude/bin/escuta-alterna.sh"
  local ativas
  ativas=$(gsettings get org.gnome.shell enabled-extensions)
  ativas=$(python3 -c "import ast,sys; l=ast.literal_eval(sys.argv[1].replace('@as ','')); u=sys.argv[2]; l.append(u) if u not in l else None; print(l)" "$ativas" "$GNOME_EXT")
  gsettings set org.gnome.shell enabled-extensions "$ativas"
  ln -sfn "$HOME/.claude/bin/atualiza.sh" "$HOME/.local/bin/manha"
}

servicos() {
  passo "Serviço do \"Ei Jarvis\""
  # o microfone sem eco antes da escuta: ela escolhe o microfone quando sobe
  systemctl --user enable --now jarvis-aec.service
  systemctl --user enable --now jarvis-escuta.service
  # sessão do Claude sempre aberta: ~2 s a menos por pergunta
  systemctl --user enable --now jarvis-cerebro.service
  # voz já carregada (Piper aberto + timbre): ~0,6 s a menos por frase
  systemctl --user enable --now jarvis-fala.service
}

trabalho() {
  passo "Timers do trabalho (GitLab às 7h, lembrete de reunião)"
  systemctl --user enable --now sydle-manha.timer sydle-lembrete.timer
  FALTA+=("guardar o token do GitLab: secret-tool store --label=GitLab service sydle-daily account gitlab")
  FALTA+=("configurar a agenda: python3 ~/.claude/bin/agenda.py --configurar")
}

spotify() {
  passo "Spotify (spotifyd $SPOTIFYD_VERSAO, o dispositivo \"Jarvis\")"
  local pasta="$DADOS/spotifyd" base="https://github.com/Spotifyd/spotifyd/releases/download/$SPOTIFYD_VERSAO"
  if [ ! -x "$pasta/spotifyd" ]; then
    local tmp
    tmp=$(mktemp -d)
    curl -sSL -o "$tmp/s.tar.gz" "$base/spotifyd-linux-x86_64-full.tar.gz"
    curl -sSL -o "$tmp/s.sha512" "$base/spotifyd-linux-x86_64-full.sha512"
    (cd "$tmp" && [ "$(sha512sum s.tar.gz | cut -d' ' -f1)" = "$(cut -d' ' -f1 s.sha512)" ]) \
      || { echo "checksum do spotifyd não confere"; rm -rf "$tmp"; return 1; }
    mkdir -p "$pasta"
    tar xzf "$tmp/s.tar.gz" -C "$pasta"
    rm -rf "$tmp"
  fi
  # login do player (uma vez): ele abre a página do Spotify e guarda em ~/.cache/spotifyd
  [ -e "$HOME/.cache/spotifyd/oauth/credentials.json" ] ||
    "$pasta/spotifyd" authenticate --cache-path "$HOME/.cache/spotifyd"
  systemctl --user enable --now jarvis-spotifyd.service
  FALTA+=("Spotify: criar um app em developer.spotify.com (redirect http://127.0.0.1:8899/callback), pôr o Client ID em JARVIS_SPOTIFY_CLIENT_ID e JARVIS_MUSICA=spotify no local.env, e rodar ~/.claude/bin/spotify.py configurar")
}

conferir_sistema() {
  for prog in wl-copy secret-tool pw-record notify-send gtk-launch claude; do
    command -v "$prog" >/dev/null || FALTA+=("instalar $prog")
  done
}

resumo() {
  passo "Falta fazer à mão"
  FALTA+=("Chrome: chrome://extensions → Modo do desenvolvedor → Carregar sem compactação → $DADOS/extensao")
  FALTA+=("sair da sessão e entrar de novo, para o GNOME carregar o ponto do Jarvis na barra")
  printf ' - %s\n' "${FALTA[@]}"
}

case "${1:-tudo}" in
  links) links ;;
  trabalho) links; trabalho; resumo ;;
  spotify) links; spotify; resumo ;;
  tudo) links; python_venv; voz; whisper; gnome; servicos; conferir_sistema; resumo ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac
