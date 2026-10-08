#!/usr/bin/env python3
"""A trilha do bom dia em arquivo: toca do começo, na hora, sem janela e com o volume na mão.

    trilha.py baixar "highway to hell"    baixa o áudio uma vez (uso pessoal, nesta máquina)
    trilha.py tocar ["busca"] [nivel]     toca o arquivo (padrão: JARVIS_MUSICA_BOM_DIA)
    trilha.py parar
    trilha.py ativa                        imprime "sim" se está tocando

Antes a trilha vinha do YouTube Music: continuava de onde tinha parado, abria janela por cima da
tela e o volume brigava com o stream do Chrome (que nasce em 100% e é recriado). Do arquivo, o
stream é nosso ("Jarvis trilha"), começa do zero e o Jarvis controla o volume pelo wpctl.

Arquivos em ~/.local/share/jarvis/trilhas/<nome>.mp3. O download usa o yt-dlp e o ffmpeg do
venv do Jarvis, a partir do primeiro resultado de música do YouTube Music para a busca.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

BIN = os.path.dirname(os.path.realpath(__file__))
PASTA = os.path.expanduser("~/.local/share/jarvis/trilhas")
VENV = os.path.expanduser("~/.local/share/jarvis/venv/bin")
PID = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis", "trilha.pid")
NOME_NO_SOM = "Jarvis trilha"  # como o stream aparece no wpctl status


def _modulo(nome):
    spec = importlib.util.spec_from_file_location(nome, os.path.join(BIN, f"{nome}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


config = _modulo("config")


def nome_arquivo(busca):
    s = unicodedata.normalize("NFKD", busca).encode("ascii", "ignore").decode().lower()
    return os.path.join(PASTA, re.sub(r"[^a-z0-9]+", "-", s).strip("-") + ".mp3")


def arquivo(busca=None):
    caminho = nome_arquivo(busca or config.get("JARVIS_MUSICA_BOM_DIA", "highway to hell"))
    return caminho if os.path.exists(caminho) else None


def baixar(busca):
    acao = _modulo("acao")
    achado = next(iter(acao.buscar_ytm(busca, "musica")), None)
    if not achado or not achado[2]:
        return None
    ffmpeg = subprocess.run([os.path.join(VENV, "python"), "-c",
                             "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
                            capture_output=True, text=True).stdout.strip()
    destino = nome_arquivo(busca)
    os.makedirs(PASTA, exist_ok=True)
    subprocess.run([os.path.join(VENV, "yt-dlp"), "-q", "--no-warnings", "-f", "bestaudio", "-x",
                    "--audio-format", "mp3", "--audio-quality", "3", "--ffmpeg-location", ffmpeg,
                    "-o", destino[:-4] + ".%(ext)s", f"https://music.youtube.com/watch?v={achado[2]}"],
                   check=False)
    if not os.path.exists(destino):
        return None
    artista = (achado[1] or "").split("•")[0].strip()
    json.dump({"titulo": achado[0], "artista": artista}, open(destino[:-4] + ".json", "w"), ensure_ascii=False)
    return destino


def info(busca=None):
    """Título e artista guardados no download, para "que música é essa?"."""
    caminho = nome_arquivo(busca or config.get("JARVIS_MUSICA_BOM_DIA", "highway to hell"))
    try:
        return json.load(open(caminho[:-4] + ".json"))
    except (OSError, ValueError):
        return {"titulo": (busca or config.get("JARVIS_MUSICA_BOM_DIA", "")).capitalize(), "artista": ""}


def pid_ativo():
    try:
        pid = int(open(PID).read())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _id_no_som(espera_s=2.0):
    fim = time.time() + espera_s
    while time.time() < fim:
        status = subprocess.run(["wpctl", "status"], capture_output=True, text=True).stdout
        m = re.search(rf"^\s+(\d+)\. {re.escape(NOME_NO_SOM)}\s*$", status, re.M)
        if m:
            return m[1]
        time.sleep(0.05)
    return None


def volume(nivel, fade_s=0.0):
    """Volume da trilha na escala do wpctl (percepção). Com fade, sobe ou desce aos poucos."""
    sid = _id_no_som(0.5)
    if not sid:
        return
    if fade_s <= 0:
        subprocess.run(["wpctl", "set-volume", sid, f"{nivel:.2f}"], check=False)
        return
    atual = subprocess.run(["wpctl", "get-volume", sid], capture_output=True, text=True).stdout
    m = re.search(r"([\d.]+)", atual)
    de = float(m[1]) if m else nivel
    passos = max(1, int(fade_s / 0.1))
    for i in range(1, passos + 1):
        subprocess.run(["wpctl", "set-volume", sid, f"{de + (nivel - de) * i / passos:.2f}"], check=False)
        time.sleep(0.1)


def tocar(caminho, nivel=1.0):
    """Começa a trilha do zero e já põe no nível. Devolve o pid, ou None."""
    parar()
    proc = subprocess.Popen(["pw-play", "-P", f'{{ application.name = "{NOME_NO_SOM}" '
                             'node.name = "jarvis-trilha" state.restore-props = false }', caminho],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    os.makedirs(os.path.dirname(PID), exist_ok=True)
    open(PID, "w").write(str(proc.pid))
    volume(nivel)  # o stream nasce em 100%: em ~0,1 s já está no nível (o começo da música é baixo)
    return proc.pid


def parar():
    pid = pid_ativo()
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    try:
        os.remove(PID)
    except OSError:
        pass
    return bool(pid)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["baixar"] and len(args) > 1:
        print(baixar(" ".join(args[1:])) or "não consegui baixar")
    elif args[:1] == ["tocar"]:
        busca = args[1] if len(args) > 1 else None
        caminho = arquivo(busca)
        print(f"tocando (pid {tocar(caminho, float(args[2]) if len(args) > 2 else 1.0)})" if caminho else "sem arquivo")
    elif args[:1] == ["parar"]:
        print("parada" if parar() else "não estava tocando")
    elif args[:1] == ["ativa"]:
        print("sim" if pid_ativo() else "não")
    else:
        sys.exit(__doc__)
