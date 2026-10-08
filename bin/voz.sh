#!/usr/bin/env bash
# Ditado local: aperta uma vez para gravar, aperta de novo para transcrever.
# Tudo offline — whisper.cpp na CPU. O texto vai para a área de transferência,
# a menos que seja um comando ou uma pergunta para o Jarvis (ver jarvis.py).
#
#   voz.sh          alterna gravar/parar
#   voz.sh cancela  descarta a gravação em curso
#   voz.sh --acordado ARQ.wav   já gravado pelo escuta.py depois do "Ei Jarvis": pula a
#                               gravação, e o texto vai sempre para o Jarvis (nunca é ditado)

set -uo pipefail

MODELO="${WHISPER_MODEL:-$HOME/.local/share/whisper-models/ggml-small.bin}"
BIN="${WHISPER_BIN:-$HOME/.local/share/whisper.cpp/build/bin/whisper-cli}"
IDIOMA="${WHISPER_LANG:-pt}"
NUCLEOS="${WHISPER_THREADS:-$(nproc)}"
LIMITE_SEG="${VOZ_LIMITE:-120}"   # trava de segurança: para sozinho
# vocabulário para o whisper: sem isso, "atualiza o GitLab" virou "a tua desarba e tida é bem"
VOCABULARIO="${WHISPER_PROMPT:-Jarvis, atualiza o GitLab. Abre o VSCodium, o Obsidian, o WhatsApp. Toca no YouTube Music. Pausar música. Próxima. Me lembra daqui a 20 minutos. Lembra que. Traduz isso. O que tem na tela? Aumenta o volume. Volume 4. Que música é essa? Bom dia. Próxima reunião. MR, review, painel.}"

# o atalho do GNOME roda com PATH enxuto — garante ~/.local/bin
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac

EST="${XDG_RUNTIME_DIR:-/tmp}/voz"
PID="$EST/pid"
WAV="$EST/audio.wav"
LOG="$EST/voz.log"
mkdir -p "$EST"

# a bolha do Jarvis: uma notificação só, trocada a cada etapa (bolha.py)
aviso() { python3 "$HOME/.claude/bin/bolha.py" "$@" 2>/dev/null; }

ACORDADO=""
if [ "${1:-}" = "--acordado" ]; then
  ACORDADO=1
  WAV="$2"
fi

# --- cancelar ---
if [ "${1:-}" = "cancela" ]; then
  if [ -f "$PID" ]; then
    kill -TERM "$(cat "$PID")" 2>/dev/null
    rm -f "$PID" "$WAV"
    aviso --fim "Tudo bem, deixei pra lá."
  fi
  exit 0
fi

# --- segunda chamada: para de gravar e deixa a primeira seguir ---
if [ -z "$ACORDADO" ] && [ -f "$PID" ]; then
  kill -INT "$(cat "$PID")" 2>/dev/null
  exit 0
fi

# --- pré-requisitos ---
for alvo in "$BIN" "$MODELO"; do
  if [ ! -f "$alvo" ]; then
    aviso --fim "Estou sem o meu modelo de voz" "Falta $(basename "$alvo")."
    exit 1
  fi
done

# o ponto na barra do GNOME: volta ao cinza quando este script acaba, de qualquer jeito
ESTADO="$HOME/.claude/bin/estado.py"
trap 'python3 "$ESTADO" parado' EXIT

# --- primeira chamada: grava ---
if [ -z "$ACORDADO" ]; then
python3 "$ESTADO" ouvindo "Insert"
rm -f "$WAV"
aviso --nova --som ouvindo "Estou ouvindo…"
# o microfone sem eco (serviço jarvis-aec), se existir: a música não entra no ditado
ALVO=()
pw-cli info jarvis_mic_sem_eco >/dev/null 2>&1 && ALVO=(--target jarvis_mic_sem_eco)
pw-record --rate 16000 --channels 1 "${ALVO[@]}" "$WAV" >>"$LOG" 2>&1 &
gravador=$!
echo "$gravador" > "$PID"

# trava de segurança, caso a segunda tecla nunca venha
rm -f "$EST/limite"
( sleep "$LIMITE_SEG"; touch "$EST/limite"; kill -INT "$gravador" 2>/dev/null ) &
vigia=$!

wait "$gravador" 2>/dev/null
kill "$vigia" 2>/dev/null
rm -f "$PID"
# bateu no limite: o Insert foi esquecido ligado. Transcrever 2 min de silêncio travava a CPU
# e deixava o "Ei Jarvis" sem resposta — descarta
if [ -e "$EST/limite" ]; then
  rm -f "$EST/limite" "$WAV"
  aviso --fim "Descartei a gravação do Insert" "Ficou 2 minutos gravando sem você parar."
  exit 0
fi
fi

if [ ! -s "$WAV" ]; then
  aviso --fim "Não consegui ouvir o microfone" "Confere se ele está ligado."
  exit 1
fi

# --- transcreve ---
python3 "$ESTADO" pensando
aviso --som entendi "Um instante…"
# o Whisper sempre processa uma janela de 30 s, mesmo com 2 s de fala. --audio-ctx 512 encolhe
# a janela para ~10 s: medido 2 a 3x mais rápido (5,1 s → 2,1 s), mesmo texto. Só em fala
# curta; ditado longo segue com a janela inteira para não perder o fim
# os artistas que você ouve entram no vocabulário: nome de banda é onde o Whisper mais erra
ARTISTAS=$(grep -v '^#' "$HOME/.config/jarvis/artistas.txt" 2>/dev/null | tail -40 | paste -sd, - | sed 's/,/, /g')
[ -n "$ARTISTAS" ] && VOCABULARIO="$VOCABULARIO Artistas: $ARTISTAS."
JANELA=()
segundos=$(python3 -c 'import sys,wave; w=wave.open(sys.argv[1]); print(int(w.getnframes()/w.getframerate()))' "$WAV" 2>/dev/null || echo 99)
[ "$segundos" -le 9 ] && JANELA=(-ac 512)
texto=$("$BIN" -m "$MODELO" -f "$WAV" -l "$IDIOMA" -t "$NUCLEOS" -nt -np "${JANELA[@]}" --prompt "$VOCABULARIO" 2>>"$LOG")

# whisper marca silêncio e ruído entre colchetes ou parênteses — não é fala
texto=$(printf '%s' "$texto" \
  | sed -E 's/\[[^]]*\]//g; s/\((música|musica|risos|aplausos|ruído|ruido)[^)]*\)//gi' \
  | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')

printf '%s ouviu: %s\n' "$(date +%T)" "$texto" >>"$LOG"  # para entender o que o whisper ouviu

if [ -z "$texto" ]; then
  [ -n "$ACORDADO" ] && exit 20  # o escuta.py ouve de novo
  aviso --fim "Não ouvi nada."
  exit 0
fi

# --- entrega ---
# Comando ("bom dia", "o que temos pra hoje") ou pergunta ("Jarvis, ...")? O Jarvis cuida.
# Qualquer outra coisa é ditado, como sempre: vai para o clipboard.
JARVIS="$HOME/.claude/bin/jarvis.py"
# acordado pelo "Ei Jarvis": tudo o que vem depois é para ele
if [ -n "$ACORDADO" ]; then
  texto="Jarvis, $texto"
  # só repetiu o nome (achou que eu não tinha ouvido): sai com 20, e o escuta.py ouve de novo
  [ "$(python3 "$JARVIS" --so-ativacao "$texto")" = "sim" ] && exit 20
fi
if [ -x "$JARVIS" ] && [ "$(python3 "$JARVIS" --tipo "$texto")" != "ditado" ]; then
  aviso "Deixa comigo…" "$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import bolha; print(bolha.aspas(sys.argv[2]))' "$HOME/.claude/bin" "$texto")"
  python3 "$JARVIS" "$texto" >>"$LOG" 2>&1
  exit 0
fi

if command -v wl-copy >/dev/null; then
  printf '%s' "$texto" | wl-copy
  aviso --fim "Pronto para colar" "$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import bolha; print(bolha.aspas(sys.argv[2]))' "$HOME/.claude/bin" "$texto")"
else
  printf '%s' "$texto" > "$EST/ultimo.txt"
  aviso --fim "Anotei, mas não consegui copiar" "$texto"
fi
printf '%s\n' "$texto"
