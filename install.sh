#!/usr/bin/env bash
# Monta o Jarvis nesta máquina a partir do repositório. Pode rodar de novo à vontade.
#
#   ./install.sh            tudo: links, Python, voz, Whisper, atalhos e serviços
#   ./install.sh links      só os links (os lugares de sempre passam a apontar para cá)
#   ./install.sh trabalho   também liga os timers do GitLab e da agenda (precisa do local.env)
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
  for f in "$REPO"/config/*.txt; do
    ligar "$f" "$HOME/.config/jarvis/$(basename "$f")"
  done
  for f in "$REPO"/systemd/*; do
    ligar "$f" "$HOME/.config/systemd/user/$(basename "$f")"
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
  systemctl --user enable --now jarvis-escuta.service
}

trabalho() {
  passo "Timers do trabalho (GitLab às 7h, lembrete de reunião)"
  systemctl --user enable --now sydle-manha.timer sydle-lembrete.timer
  FALTA+=("guardar o token do GitLab: secret-tool store --label=GitLab service sydle-daily account gitlab")
  FALTA+=("configurar a agenda: python3 ~/.claude/bin/agenda.py --configurar")
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
  tudo) links; python_venv; voz; whisper; gnome; servicos; conferir_sistema; resumo ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
