# Jarvis

Assistente de voz para o meu Linux (GNOME com Wayland). Roda quase tudo localmente: a palavra de ativação, a transcrição e a voz. A parte que precisa pensar vai para o Claude Code.

## O que ele faz

- **"Ei Jarvis"** acorda o assistente, e `Insert` dita texto para o clipboard. Depois de uma resposta falada, ele ouve mais 5 segundos sem precisar do nome. Se você começar a falar enquanto ele fala, ele se cala e escuta.
- **Cérebro sempre ligado**: o serviço `jarvis-cerebro` mantém uma sessão do Claude aberta. Cada pergunta sai em ~1,5 s em vez de ~4 s, porque não paga a partida do `claude` toda vez, e a sessão acompanha a conversa. Sem ele, o Jarvis chama o `claude` direto, como antes.
- **Microfone sem eco**: o serviço `jarvis-aec` cria um microfone virtual que subtrai o que sai pelas caixas (cancelamento de eco do WebRTC, dentro do PipeWire). A escuta e o ditado usam esse microfone; o Meet e o resto continuam no normal. Sem o serviço, tudo funciona com o microfone normal.
- **Música e mídia**: toca no YouTube Music ou no YouTube, pausa, pula e controla o volume. A música abaixa sozinha enquanto ele fala.
- **Apps e sites**: abre por nome ("abre o Obsidian").
- **Trabalho**: painel do GitLab às 7h, agenda do Google e lembrete de reunião 15 minutos antes. Também cria e edita tarefas no quadro do Obsidian.
- **Lembretes e rotinas**: "me lembra daqui a 20 minutos de…", "toda sexta às 17h me lembra de…".
- **Memória**: "lembra que…" guarda o fato numa nota do vault. Ele também lembra os últimos 5 minutos de conversa.
- **Claude**: perguntas livres, busca na web, resumo da página aberta, "o que tem na minha tela?" e "traduz isso" (o texto selecionado).
- **Ponto na barra do GNOME**: mostra se ele está parado, ouvindo, pensando ou falando.

## Peças

| Pasta | O quê | Instalado em |
|---|---|---|
| `bin/` | todos os scripts | `~/.claude/bin` (link) |
| `gnome/` | extensão do ponto na barra | `~/.local/share/gnome-shell/extensions/` (link) |
| `chrome/` | extensão "Jarvis Música" (YouTube Music e leitura de página) | `~/.local/share/jarvis/extensao` (link) |
| `systemd/` | escuta do "Ei Jarvis", microfone sem eco e timers do trabalho | `~/.config/systemd/user/` (link) |
| `config/` | artistas (vocabulário do Whisper), pronúncia e o microfone sem eco (`pipewire/`) | `~/.config/jarvis/` (link) |

Os caminhos de sempre são links para este repositório. Editar em qualquer um dos dois lados é a mesma coisa.

## Fora do repositório

- `~/.config/jarvis/local.env`: endereço do GitLab, usuário e projeto, ou seja, o que é da empresa. O modelo está em `config/local.env.exemplo`.
- Tokens e credenciais ficam no chaveiro do sistema (`secret-tool`), nunca em arquivo.
- Modelos baixados (Whisper, voz, palavra de ativação) e o ambiente Python ficam em `~/.local/share`. O `install.sh` baixa tudo de novo.

## Montar em outra máquina

```bash
git clone https://github.com/<usuario>/jarvis ~/projetos/jarvis
cd ~/projetos/jarvis
./install.sh            # links, Python, voz, Whisper, atalhos e o serviço do "Ei Jarvis"
./install.sh trabalho   # só na máquina do trabalho: timers do GitLab e da agenda
```

No fim, o `install.sh` lista o que precisa ser feito à mão: carregar a extensão no Chrome, sair e entrar na sessão e instalar os pacotes do sistema que faltarem. Precisa de:

- `pipewire` (`pw-record`, `pw-play`, `wpctl`);
- `wl-clipboard`;
- `libsecret-tools` (`secret-tool`);
- `libnotify-bin`;
- o Claude Code instalado e logado;
- `cmake` e `g++` para compilar o whisper.cpp.

## Atalhos

| Tecla | Faz |
|---|---|
| `Insert` | grava e para (ditado ou pedido) |
| `Ctrl+Alt+X` | cancela a gravação |
| `Ctrl+Alt+J` | liga e desliga o "Ei Jarvis" |
| `Ctrl+Alt+M` | atualiza o painel do GitLab (também: `manha` no terminal) |
| `Ctrl+Alt+N` | captura rápida de um pedido para o quadro |

## Teste das frases

`tests/rodar.py` confere se cada frase real (tirada do `voz.log`) vira o comando certo. Rode antes de mexer no reconhecimento, e acrescente em `tests/frases.tsv` cada frase nova que der errado. `tests/rodar.py --ver "frase"` mostra o que uma frase vira hoje.

## Onde olhar quando algo dá errado

- `$XDG_RUNTIME_DIR/voz/voz.log`: o que o Whisper ouviu.
- `$XDG_RUNTIME_DIR/voz/escuta.log`: quando acordou, com qual nota e por que não ouviu.
- `gnome-extensions info jarvis@eduardo.almeida`: erro da extensão da barra.
- `$XDG_RUNTIME_DIR/voz/volume.log`: cada mudança de volume, com o valor do Jarvis e o que o GNOME mostra.
- Som do Jarvis ou do Chrome baixo demais: `wpctl status`, depois `wpctl set-volume <id> 1.0`.
