#!/usr/bin/env python3
"""As ações de sistema que o Jarvis pode fazer. Lista fechada, de propósito.

    acao.py abrir <app ou site>     "obsidian", "vs codium", "whatsapp", "gitlab"
    acao.py tocar <busca>           no YouTube Music (música ou playlist), no app dele
    acao.py tocar <busca> no youtube    primeiro vídeo do YouTube, no app do YouTube
    acao.py url <https://...>       abre um endereço no navegador (só http/https)
    acao.py midia pausar|continuar|proxima|anterior|inicio
    acao.py agora                   o que está tocando (ou pausado), e onde
    acao.py volume mais|menos|muito mais|muito menos|maximo|minimo|metade|mudo|<0-10 ou %>
    acao.py apps                    lista o que o "abrir" entende

Cada ação imprime uma frase curta, para ser falada. Sai com 1 se não entendeu.

É a única porta de sistema do Jarvis: o jarvis.py chama estas funções direto, e o
Claude (`claude -p`) só tem permissão para rodar este arquivo. Não tem ação de
fechar, apagar nem rodar comando solto — para ganhar uma, ela entra aqui.
"""
import difflib
import fcntl
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

# o que se fala → desktop id (gtk-launch) ou endereço. Ganha da busca por nome.
APELIDOS = {
    "codium": ["vs codium", "vscodium", "codium", "vs code", "vscode", "visual studio code", "code",
               "editor de codigo", "vis codium", "vias codium"],
    "obsidian": ["obsidian", "obsidiana", "obsidien", "vault", "o vault", "cofre", "notas"],
    "google-chrome": ["chrome", "google chrome", "navegador", "browser", "crome"],
    "org.gnome.Ptyxis": ["terminal", "console"],
    "org.gnome.Nautilus": ["arquivos", "gerenciador de arquivos", "pastas", "nautilus", "explorador"],
    "org.gnome.Calculator": ["calculadora"],
    "org.gnome.Settings": ["configuracoes", "configuracao", "ajustes", "settings"],
    "org.gnome.clocks": ["relogio", "cronometro", "alarme"],
    "org.gnome.TextEditor": ["editor de texto", "bloco de notas"],
    "chrome-hnpfjngllnobngcgfapefoaidbinmjnm-Default": ["whatsapp", "whats", "zap", "zapzap", "whatsapp web"],
    "chrome-agimnkijcaahngcdmfeangaknmldooml-Default": ["youtube", "you tube", "iutubi"],
    "chrome-cinhimbnkkaeohfgghhklpknlkffjgod-Default": ["youtube music", "youtube musica", "you tube music",
                                                        "yt music", "music"],
    "https://calendar.google.com": ["agenda", "calendario", "google agenda"],
    "https://mail.google.com": ["gmail", "email", "e-mail", "meu email", "caixa de entrada"],
}
NOME_FALADO = {"codium": "o VSCodium", "obsidian": "o Obsidian", "google-chrome": "o Chrome",
               "org.gnome.Ptyxis": "o terminal", "org.gnome.Nautilus": "os arquivos",
               "org.gnome.Calculator": "a calculadora", "org.gnome.Settings": "as configurações",
               "org.gnome.clocks": "o relógio", "org.gnome.TextEditor": "o editor de texto",
               "chrome-hnpfjngllnobngcgfapefoaidbinmjnm-Default": "o WhatsApp",
               "chrome-agimnkijcaahngcdmfeangaknmldooml-Default": "o YouTube",
               "chrome-cinhimbnkkaeohfgghhklpknlkffjgod-Default": "o YouTube Music",
               "https://calendar.google.com": "a agenda", "https://mail.google.com": "o Gmail"}
ARTISTAS = os.path.expanduser("~/.config/jarvis/artistas.txt")
import importlib.util as _iu
_spec_c = _iu.spec_from_file_location("config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py"))
config = _iu.module_from_spec(_spec_c)
_spec_c.loader.exec_module(config)  # ~/.config/jarvis/local.env: o que é da empresa fica fora do repositório
# "abre o GitLab": o endereço dos MRs vem da configuração da máquina
if config.get("GITLAB_MRS_URL"):
    APELIDOS[config.get("GITLAB_MRS_URL")] = ["gitlab", "git lab", "merge requests", "mrs"]
    NOME_FALADO[config.get("GITLAB_MRS_URL")] = "o GitLab"
MARCA_SO_MIDIA = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis", "so-midia")
PASTAS_DESKTOP = ["~/.local/share/applications", "/usr/share/applications",
                  "/var/lib/flatpak/exports/share/applications", "/var/lib/snapd/desktop/applications"]


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", sem_acento.lower())).strip()


# ---------------------------------------------------------------- abrir

def apps_instalados():
    """{desktop id: nome} dos apps que aparecem no menu."""
    apps = {}
    for pasta in PASTAS_DESKTOP:
        for f in glob.glob(os.path.join(os.path.expanduser(pasta), "*.desktop")):
            try:
                texto = open(f, encoding="utf-8").read()
            except OSError:
                continue
            if re.search(r"^NoDisplay=true", texto, re.M):
                continue
            nome = (re.search(r"^Name\[pt_BR\]=(.+)$", texto, re.M) or re.search(r"^Name=(.+)$", texto, re.M))
            if nome:
                apps.setdefault(os.path.basename(f)[:-8], nome[1].strip())
    return apps


def resolver(alvo):
    """'o vs codium' → ('codium', 'o VSCodium'). None se não reconhece."""
    alvo = re.sub(r"^(o|a|os|as|meu|minha|um|uma)\s+", "", normalizar(alvo))
    alvo = re.sub(r"\s+(pra mim|por favor|ai)$", "", alvo)
    if not alvo:
        return None
    apps = apps_instalados()
    falado = {}
    for destino, nomes in APELIDOS.items():
        if destino.startswith("http") or destino in apps:
            for n in nomes:
                falado[n] = destino
    for destino, nome in apps.items():
        falado.setdefault(normalizar(nome), destino)
    destino = falado.get(alvo)
    if not destino:
        # a mão chega até "obsidiam"; o corte alto evita abrir app errado por engano
        perto = difflib.get_close_matches(alvo, falado, n=1, cutoff=0.8)
        destino = falado[perto[0]] if perto else None
    if not destino:
        return None
    return destino, NOME_FALADO.get(destino) or apps.get(destino, destino)


def soltar(cmd):
    """Abre e não espera: o app vive depois que o Jarvis termina."""
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def abrir(alvo):
    achado = resolver(alvo)
    if not achado:
        return None
    destino, nome = achado
    if destino == "chrome-cinhimbnkkaeohfgghhklpknlkffjgod-Default":
        r = musica.pedir({"acao": "estado"})  # pela extensão: já tem aba do YouTube Music?
        if r and r.get("aberta"):
            return "O YouTube Music já está aberto."
    soltar(["xdg-open", destino] if destino.startswith("http") else ["gtk-launch", destino])
    return f"Abrindo {nome}."


def url(endereco):
    if not re.match(r"^https?://", endereco):
        return None
    soltar(["xdg-open", endereco])
    return "Abrindo no navegador."


# ---------------------------------------------------------------- youtube

def buscar_youtube(busca):
    """(id, título) do primeiro vídeo. Lê a página de resultados: sem API, sem chave."""
    req = urllib.request.Request(
        "https://www.youtube.com/results?search_query=" + urllib.parse.quote(busca),
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "pt-BR"})
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode("utf-8", "replace")
    videos = list(re.finditer(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html))
    if not videos:
        return None
    # o título só vale dentro do bloco do próprio vídeo; sem o corte, pegava o do vizinho
    fim = videos[1].start() if len(videos) > 1 else len(html)
    t = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', html[videos[0].start():fim])
    return videos[0][1], json.loads(f'"{t[1]}"') if t else busca


# ---------------------------------------------------------------- youtube music

# a busca da própria página do YouTube Music (API interna, sem chave). Os `params` são os
# filtros "Músicas" e "Playlists" da tela de busca: sem eles, o primeiro resultado mistura itens
YTM_BUSCA = "https://music.youtube.com/youtubei/v1/search?prettyPrint=false"
YTM_FILTRO = {"musica": "EgWKAQIIAWoMEA4QChADEAQQCRAF",
              "playlist": "EgeKAQQoADgBagwQDhAKEAMQBBAJEAU=",       # as do YouTube Music
              "comunidade": "EgeKAQQoAEABagwQDhAKEAMQBBAJEAU="}    # as de usuários
CARA_DE_PLAYLIST = re.compile(r"\b(playlist|playlists|mix|radio|rádio|musicas|músicas|som de|para estudar|pra estudar|"
                              r"para trabalhar|pra trabalhar|para relaxar|pra relaxar)\b", re.I)


def _itens_ytm(x):
    if isinstance(x, dict):
        if "musicResponsiveListItemRenderer" in x:
            yield x["musicResponsiveListItemRenderer"]
            return
        for v in x.values():
            yield from _itens_ytm(v)
    elif isinstance(x, list):
        for v in x:
            yield from _itens_ytm(v)


def buscar_ytm(busca, filtro):
    """[(título, subtítulo, videoId, playlistId)] na ordem do YouTube Music."""
    corpo = {"context": {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.20250101.01.00",
                                    "hl": "pt", "gl": "BR"}},
             "query": busca, "params": YTM_FILTRO[filtro]}
    req = urllib.request.Request(YTM_BUSCA, data=json.dumps(corpo).encode(), headers={
        "Content-Type": "application/json", "User-Agent": "Mozilla/5.0", "Origin": "https://music.youtube.com"})
    with urllib.request.urlopen(req, timeout=10) as r:
        dados = json.load(r)
    saida = []
    for it in _itens_ytm(dados):
        cols = [c["musicResponsiveListItemFlexColumnRenderer"]["text"].get("runs") or []
                for c in it.get("flexColumns") or []]
        titulo = cols[0][0].get("text") if cols and cols[0] else ""
        sub = "".join(x.get("text", "") for x in cols[1]) if len(cols) > 1 else ""
        vid = (it.get("playlistItemData") or {}).get("videoId")
        browse = ((it.get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("browseId") or ""
        lista = browse[2:] if browse.startswith("VL") else None
        if vid or lista:
            saida.append((titulo, sub, vid, lista))
    return saida


def app_web(nome):
    """O --app-id do app que o Chrome instalou (YouTube, YouTube Music...), ou None."""
    for destino, n in apps_instalados().items():
        if normalizar(n) == normalizar(nome) and destino.startswith("chrome-"):
            return destino.split("-")[1]
    return None


def pausar_o_que_toca(fora_da_musica=False):
    """Antes de tocar outra coisa, pausa o que está tocando, para não tocarem dois juntos.
    fora_da_musica: não mexe no YouTube Music — nele a faixa é trocada no lugar."""
    musica_agora = musica_pela_extensao() if fora_da_musica else None
    for p in players():
        e = _estado(p)
        if e["status"] != "Playing":
            continue
        if musica_agora and e["titulo"] == musica_agora["titulo"]:
            continue  # é a sessão do próprio YouTube Music vista pelo MPRIS
        _gdbus("--dest", p, "--object-path", "/org/mpris/MediaPlayer2", "--method",
               "org.mpris.MediaPlayer2.Player.Pause")


def abrir_no_app(nome, endereco):
    """Abre o endereço dentro do app do Chrome; sem o app, numa aba comum."""
    pausar_o_que_toca()
    app = app_web(nome)
    if app:
        soltar(["google-chrome", "--profile-directory=Default", f"--app-id={app}",
                f"--app-launch-url-for-shortcuts-menu-item={endereco}"])
    else:
        soltar(["xdg-open", endereco])


_spec_m = importlib.util.spec_from_file_location("musica", os.path.join(os.path.dirname(os.path.abspath(__file__)), "musica.py"))
musica = importlib.util.module_from_spec(_spec_m)
_spec_m.loader.exec_module(musica)


def abrir_musica(endereco):
    """Com a extensão Jarvis Música: troca a faixa na aba do YouTube Music que já está aberta.
    Sem extensão, ou sem aba aberta: abre o app do YouTube Music (janela nova)."""
    pausar_o_que_toca(fora_da_musica=True)
    r = musica.pedir({"acao": "tocar", "url": endereco})
    if not (r and r.get("ok") and r.get("aberta")):
        abrir_no_app("YouTube Music", endereco)


def artistas_conhecidos():
    try:
        return [l.strip() for l in open(ARTISTAS, encoding="utf-8") if l.strip() and not l.startswith("#")]
    except OSError:
        return []


def aprender_artistas(texto):
    """'Rionegro & Solimões e Bruno & Marrone' → cada um entra na lista, se ainda não está."""
    novos = [a.strip() for a in re.split(r"\s+e\s+|,", texto) if len(a.strip()) > 1]
    conhecidos = {normalizar(a) for a in artistas_conhecidos()}
    novos = [a for a in novos if normalizar(a) not in conhecidos]
    if novos:
        os.makedirs(os.path.dirname(ARTISTAS), exist_ok=True)
        with open(ARTISTAS, "a", encoding="utf-8") as f:
            f.write("".join(f"{a}\n" for a in novos))


def corrigir_artista(busca):
    """'bruno e manon' → 'bruno e marrone': troca o trecho que parece um artista conhecido."""
    palavras = normalizar(busca).split()
    melhor = (0.0, None, 0, 0)
    for artista in artistas_conhecidos():
        alvo = normalizar(artista.replace("&", " e "))
        n = len(alvo.split())
        for tam in {max(1, n - 1), n, n + 1}:
            for i in range(0, max(1, len(palavras) - tam + 1)):
                trecho = " ".join(palavras[i:i + tam])
                # sem espaço também: o whisper junta o nome ("brunimahoni")
                r = max(difflib.SequenceMatcher(None, trecho, alvo).ratio(),
                        difflib.SequenceMatcher(None, trecho.replace(" ", ""), alvo.replace(" ", "")).ratio())
                if r > melhor[0]:
                    melhor = (r, artista, i, tam)
    r, artista, i, tam = melhor
    if artista and 0.75 <= r < 1.0:
        return " ".join(palavras[:i] + [artista] + palavras[i + tam:])
    return busca


def tocar_music(busca):
    busca = corrigir_artista(busca)
    playlist = bool(CARA_DE_PLAYLIST.search(busca))
    termo = re.sub(r"\b(a |uma )?(playlist|playlists)( de| do| da)?\b", "", busca, flags=re.I).strip() or busca
    try:
        ordem = ("playlist", "comunidade") if playlist else ("musica",)
        achado = next((r for f in ordem for r in buscar_ytm(termo, f)[:1]), None)
    except (OSError, ValueError, KeyError):
        achado = None
    if not achado:
        abrir_musica("https://music.youtube.com/search?q=" + urllib.parse.quote(busca))
        return f"Não achei direto. Abri a busca por {busca} no YouTube Music."
    titulo, sub, vid, lista = achado
    if lista:
        abrir_musica(f"https://music.youtube.com/watch?list={lista}")
        return f"Tocando a playlist {curto(titulo)}."
    abrir_musica(f"https://music.youtube.com/watch?v={vid}")
    artista = sub.split("•")[0].strip()
    aprender_artistas(artista)
    return f"Tocando {curto(titulo)}" + (f", de {artista}." if artista else ".")


# ---------------------------------------------------------------- tocar

ONDE = re.compile(r"\s+(no|do|pelo|na) (youtube music|youtube musica|you tube music|yt music|music|youtube|you tube)$", re.I)
# sem dizer onde, toca no YouTube Music: pedido de música é o caso comum; "no YouTube" vai para o vídeo
PADRAO_TOCAR = "youtube music"


def curto(titulo, palavras=8):
    titulo = re.sub(r"\s*[\(\[](official|oficial|video|clipe|lyric|hd|4k|remaster)[^)\]]*[\)\]]", "", titulo, flags=re.I)
    p = titulo.split()
    return " ".join(p[:palavras]) + ("…" if len(p) > palavras else "")  # título inteiro cansa de ouvir


def tocar(busca):
    """'X no youtube music', 'X no youtube' ou só 'X' (vai para o PADRAO_TOCAR)."""
    busca = busca.strip()
    m = ONDE.search(busca)
    onde = normalizar(m[2]) if m else PADRAO_TOCAR
    busca = ONDE.sub("", busca).strip()
    if not busca:
        return None
    if onde not in ("youtube", "you tube"):
        return tocar_music(busca)
    try:
        achado = buscar_youtube(busca)
    except OSError:
        achado = None
    if not achado:  # sem rede ou a página mudou: pelo menos a busca abre
        abrir_no_app("YouTube", "https://www.youtube.com/results?search_query=" + urllib.parse.quote(busca))
        return f"Não achei direto. Abri a busca por {busca}."
    vid, titulo = achado
    abrir_no_app("YouTube", f"https://www.youtube.com/watch?v={vid}")
    return f"Tocando {curto(titulo)}."


# ---------------------------------------------------------------- mídia e volume

def _gdbus(*args):
    return subprocess.run(["gdbus", "call", "--session", *args], capture_output=True, text=True).stdout


def players():
    """Os players MPRIS da sessão (o Chrome aparece como chromium.instanceNNN)."""
    nomes = _gdbus("--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus",
                   "--method", "org.freedesktop.DBus.ListNames")
    return re.findall(r"'(org\.mpris\.MediaPlayer2\.[^']+)'", nomes)


def _estado(p):
    """{status, titulo, artista, app} de um player."""
    tudo = _gdbus("--dest", p, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                  "org.freedesktop.DBus.Properties.GetAll", "org.mpris.MediaPlayer2.Player")
    app = _gdbus("--dest", p, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                 "org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2", "Identity")
    g = lambda padrao: (re.search(padrao, tudo) or [None, ""])[1]
    return {"player": p, "status": g(r"'PlaybackStatus': <'(\w+)'>"),
            "titulo": g(r"'xesam:title': <'((?:[^'\\]|\\.)*)'>").replace("\\'", "'"),
            "artista": g(r"'xesam:artist': <\['((?:[^'\\]|\\.)*)'").replace("\\'", "'"),
            "app": (re.search(r"<'([^']*)'>", app) or [None, ""])[1]}


EXTENSAO = "extensao:youtube-music"  # "player" do YouTube Music visto pela extensão


def musica_pela_extensao():
    """O YouTube Music como um player, pela extensão Jarvis Música; None sem extensão ou sem aba."""
    r = musica.pedir({"acao": "estado"}, limite=4)
    if not (r and r.get("ok") and r.get("aberta")):
        return None
    return {"player": EXTENSAO, "status": "Playing" if r.get("tocando") else "Paused",
            "titulo": r.get("titulo", ""), "artista": r.get("artista", ""), "app": "YouTube Music"}


def tocando():
    """O player que está em play; se nenhum, o pausado (é ele que "continua" quer); ou None.

    O YouTube Music entra pela extensão, e ganha no empate. O Chrome mostra ao MPRIS uma sessão
    só, que pode ser um podcast pausado enquanto o YouTube Music toca: pela extensão não erra."""
    estados = [_estado(p) for p in players()]
    ytm = musica_pela_extensao()
    if ytm:
        # a mesma sessão vista pelo MPRIS sai, para não aparecer duas vezes
        estados = [ytm] + [e for e in estados if e["titulo"] != ytm["titulo"]]
    for status in ("Playing", "Paused"):
        for e in estados:
            if e["status"] == status:
                return e
    return estados[0] if estados else None


def descrever(e):
    """'Another World, de MEDUZA e HAYLA' — sem o '(Official Video)' que ninguém fala."""
    if not e or not e["titulo"]:
        return ""
    return curto(e["titulo"]) + (f", de {e['artista']}" if e["artista"] else "")


METODOS = {"pausar": ("Pause", "Pausei"), "continuar": ("Play", "Continuando"),
           "proxima": ("Next", "Próxima"), "anterior": ("Previous", "Voltei"),
           "inicio": (None, "Do começo")}


def som_saindo(app="Google Chrome"):
    """O app está mandando som agora? Olha o stream no PipeWire, não o MPRIS."""
    status = subprocess.run(["wpctl", "status"], capture_output=True, text=True).stdout
    for bloco in re.findall(rf"^\s+\d+\. {re.escape(app)}\s*\n((?:\s{{11,}}\d+\..*\n?)+)", status, re.M):
        if re.search(r"output_\w+.*\[active\]", bloco):
            return True
    return False


def midia(o_que):
    """Age só no player que está tocando (ou no pausado), e diz o que ficou tocando.

    Pegadinha do Chrome: ele mostra ao sistema UMA sessão de mídia só — a última que pediu
    áudio —, mesmo com várias tocando. Dá play e pause num podcast no YouTube e o YouTube
    Music, que segue tocando, some do controle. Nesse caso, avisa em vez de fingir que pausou."""
    if o_que not in METODOS:
        return None
    metodo, frase = METODOS[o_que]
    e = tocando()
    if not e:
        return "Não tem nada tocando agora."
    p = e["player"]
    if p == EXTENSAO:
        if o_que == "pausar" and e["status"] != "Playing":
            return f"Já está pausado: {descrever(e)}."
        if o_que == "continuar" and e["status"] == "Playing":
            return f"Já está tocando: {descrever(e)}."
        r = musica.pedir({"acao": o_que})
        if not (r and r.get("ok")):
            return "Não consegui falar com o YouTube Music agora."
        o_que_toca = descrever({"titulo": r.get("titulo", ""), "artista": r.get("artista", "")})
        return f"{frase}: {o_que_toca}." if o_que_toca else f"{frase}."
    if e["status"] != "Playing" and o_que != "continuar":
        if e["app"] == "Chrome" and som_saindo():
            return (f"O Chrome está me mostrando {descrever(e) or 'outro vídeo'}, que já está pausado. "
                    "O som vem de outra aba ou app, e o Chrome não me deixa controlar esse.")
        if o_que == "pausar":
            return f"Já está pausado: {descrever(e)}." if descrever(e) else "Já está pausado."
    if o_que == "continuar" and e["status"] == "Playing":
        return f"Já está tocando: {descrever(e)}."
    if o_que == "inicio":  # Seek negativo grande: volta para o zero sem precisar do trackid
        _gdbus("--dest", p, "--object-path", "/org/mpris/MediaPlayer2", "--method",
               "org.mpris.MediaPlayer2.Player.Seek", "--", "-999999999999")
    else:
        _gdbus("--dest", p, "--object-path", "/org/mpris/MediaPlayer2", "--method",
               f"org.mpris.MediaPlayer2.Player.{metodo}")
    if o_que in ("proxima", "anterior"):
        # a faixa nova leva um instante para aparecer no MPRIS
        antes = e["titulo"]
        for _ in range(20):
            time.sleep(0.15)
            e = _estado(p)
            if e["titulo"] and e["titulo"] != antes:
                break
    o_que_toca = descrever(e)
    return f"{frase}: {o_que_toca}." if o_que_toca else f"{frase}."


def agora():
    e = tocando()
    if not e or not e["titulo"]:
        return "Não tem nada tocando agora."
    onde = f" no {e['app']}" if e["app"] else ""
    if e["status"] == "Playing":
        return f"Está tocando {descrever(e)}{onde}."
    return f"Está pausado: {descrever(e)}{onde}."


def _volume_atual(saida="@DEFAULT_AUDIO_SINK@"):
    atual = subprocess.run(["wpctl", "get-volume", saida], capture_output=True, text=True).stdout
    m = re.search(r"([\d.]+)", atual)
    return round(float(m[1]) * 100) if m else None


def _registrar_volume(pedido, v):
    """Diagnóstico: o volume que o Jarvis pôs e o que o GNOME (protocolo do PulseAudio) mostra.
    Um dia ficaram diferentes e não deu para reproduzir no alto-falante; suspeita: fone Bluetooth."""
    try:
        saida = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
                               timeout=3).stdout
        nome = (re.search(r'node\.description = "([^"]+)"', saida) or [None, "?"])[1]
        gnome = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], capture_output=True, text=True,
                               timeout=3).stdout
        gnome = (re.search(r"(\d+)%", gnome) or [None, "?"])[1]
        caminho = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "voz", "volume.log")
        with open(caminho, "a") as f:
            f.write(f"{time.strftime('%F %T')} pedido={pedido} jarvis={v}% gnome={gnome}% saida={nome}\n")
    except (OSError, subprocess.TimeoutExpired):
        pass


def volume(quanto):
    """mais|menos|muito mais|muito menos|maximo|minimo|metade|mudo|N.
    N até 10 é a escala da Alexa (volume 5 = 50%); acima disso, porcentagem."""
    saida = "@DEFAULT_AUDIO_SINK@"
    if quanto == "mudo":
        subprocess.run(["wpctl", "set-mute", saida, "toggle"], check=False)
        mudo = "MUTED" in subprocess.run(["wpctl", "get-volume", saida], capture_output=True, text=True).stdout
        return "Som desligado." if mudo else "Som de volta."
    passo = {"mais": "10%+", "menos": "10%-", "muito mais": "25%+", "muito menos": "25%-",
             "maximo": "100%", "minimo": "10%", "metade": "50%"}.get(quanto)
    if not passo:
        if not re.fullmatch(r"\d{1,3}", quanto or ""):
            return None
        n = int(quanto)
        passo = f"{min(n * 10 if n <= 10 else n, 100)}%"
    subprocess.run(["wpctl", "set-mute", saida, "0"], check=False)
    subprocess.run(["wpctl", "set-volume", "-l", "1.0", saida, passo], check=False)
    v = _volume_atual()
    _registrar_volume(quanto, v)
    return f"Volume em {v} por cento." if v is not None else "Pronto."


class abaixar_musica:
    """Enquanto o Jarvis fala (ou ouve), a música baixa e volta depois — o "ducking" da Alexa.

    Duas armadilhas já morderam, e este desenho evita as duas:
    - O WirePlumber memoriza o volume de cada app. Stream que some abaixado deixa o próximo
      nascer abaixado. Por isso os sons do próprio Jarvis (pw-play) nunca entram, e na volta o
      volume vai também para o stream novo do mesmo app (o Chrome troca de stream à toa).
    - Processo morto no meio (o serviço reiniciou com o Jarvis falando) nunca devolvia o volume;
      o ducking seguinte lia o volume já baixo como "normal" e abaixava em cima: 25% de 25% de 25%,
      e o Meet ficou quase mudo. Agora o volume ORIGINAL fica num arquivo, com um contador de
      quem está abaixando (a escuta e o jarvis.py são processos diferentes). Quem entra depois
      reaproveita o original; arquivo velho é sobra de processo morto e é restaurado."""

    ESTADO = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis", "ducking.json")
    VELHO_S = 120  # ninguém fala ou ouve por 2 minutos seguidos: arquivo mais velho é sobra

    def __init__(self, nivel=0.25):
        self.nivel, self.entrou = nivel, False

    # -- estado compartilhado, sob trava
    @classmethod
    def _com_trava(cls, mexer):
        os.makedirs(os.path.dirname(cls.ESTADO), exist_ok=True)
        with open(cls.ESTADO + ".trava", "w") as t:
            fcntl.flock(t, fcntl.LOCK_EX)
            try:
                estado = json.load(open(cls.ESTADO))
                if time.time() - os.path.getmtime(cls.ESTADO) > cls.VELHO_S:
                    cls._restaurar(estado["originais"])  # sobra de processo morto
                    estado = None
            except (OSError, ValueError, KeyError):
                estado = None
            estado = mexer(estado or {"originais": {}, "ativos": 0})
            if estado is None:
                try:
                    os.remove(cls.ESTADO)
                except OSError:
                    pass
            else:
                json.dump(estado, open(cls.ESTADO, "w"))

    @classmethod
    def _restaurar(cls, originais):
        for sid, nome in cls.streams_de_saida():
            if nome in originais:
                subprocess.run(["wpctl", "set-volume", sid, originais[nome]], check=False)

    @classmethod
    def recuperar(cls):
        """Para a escuta chamar ao subir: devolve volume que ficou abaixado por processo morto."""
        def mexer(estado):
            cls._restaurar(estado["originais"])
            return None
        cls._com_trava(mexer)

    def __enter__(self):
        e = tocando()
        if not e or e["status"] != "Playing":
            return self

        def mexer(estado):
            for sid, nome in self.streams_de_saida():
                if nome not in estado["originais"]:
                    v = subprocess.run(["wpctl", "get-volume", sid], capture_output=True, text=True).stdout
                    m = re.search(r"([\d.]+)", v)
                    if not m:
                        continue
                    estado["originais"][nome] = m[1]
                original = float(estado["originais"][nome])
                subprocess.run(["wpctl", "set-volume", sid, f"{original * self.nivel:.2f}"], check=False)
            estado["ativos"] += 1
            return estado

        self._com_trava(mexer)
        self.entrou = True
        return self

    def __exit__(self, *_):
        if not self.entrou:
            return

        def mexer(estado):
            estado["ativos"] -= 1
            if estado["ativos"] > 0:
                return estado  # outro processo ainda está falando: ele devolve
            self._restaurar(estado["originais"])
            return None

        self._com_trava(mexer)

    @staticmethod
    def streams_de_saida():
        """[(id, nome)] dos streams que TOCAM som (têm porta output_), menos os do Jarvis.
        Os de entrada — o microfone do Meet, a escuta do Jarvis — também ficam de fora."""
        status = subprocess.run(["wpctl", "status"], capture_output=True, text=True).stdout
        achados = []
        for bloco in re.findall(r"Streams:\n((?:\s+\d+\..*\n?)+)", status):
            atual = None
            for linha in bloco.splitlines():
                m = re.match(r"^\s{6,10}(\d+)\. (?!output_|input_|monitor_)(.+?)\s*$", linha)
                if m:
                    atual = (m[1], m[2]) if m[2] not in ("pw-play", "pw-cat", "piper") else None
                elif atual and re.match(r"^\s+\d+\. output_", linha) and atual not in achados:
                    achados.append(atual)
        return achados


# ---------------------------------------------------------------- cli

def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    acao, resto = args[0], " ".join(args[1:]).strip()
    if acao == "apps":
        for destino, nomes in APELIDOS.items():
            print(f"{destino}: {', '.join(nomes)}")
        return 0
    if acao == "agora":
        print(agora())
        return 0
    feito = {"abrir": abrir, "tocar": tocar, "url": url, "midia": midia, "volume": volume}.get(acao)
    frase = feito(resto) if feito else None
    if frase and acao in ("midia", "volume"):
        # o Claude do Jarvis usou só controle de mídia: o jarvis.py vê esta marca e não fala
        os.makedirs(os.path.dirname(MARCA_SO_MIDIA), exist_ok=True)
        open(MARCA_SO_MIDIA, "w").close()
    if not frase:
        print(f"não entendi: {acao} {resto}".strip())
        return 1
    print(frase)
    return 0


if __name__ == "__main__":
    sys.exit(main())
