#!/usr/bin/env python3
"""Abre a tela de captura do GNOME; você escolhe tela, janela ou pedaço e aperta Enter.

    print.py [destino.png]       imprime o caminho do PNG; sai com 1 se você cancelou (Esc)

No GNOME com Wayland, programa comum não tira print sozinho: o Shell só aceita os da lista
dele, e o portal (org.freedesktop.portal.Screenshot) com interactive=false responde 2 (erro)
para quem não é Flatpak — testado em 07/10. Com interactive=true o GNOME mostra a própria
tela de captura, e aí funciona. Um passo a mais, mas dá para mostrar só o pedaço que importa.
"""
import os
import shutil
import sys
import time
import urllib.parse

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

DESTINO = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis", "tela.png")
LIMITE_S = 60  # tempo para você escolher o pedaço e apertar Enter


def tirar(destino=DESTINO):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    token = f"jarvis{int(time.time() * 1000)}"
    remetente = bus.get_unique_name()[1:].replace(".", "_")
    caminho_pedido = f"/org/freedesktop/portal/desktop/request/{remetente}/{token}"
    loop, resultado = GLib.MainLoop(), {}

    def resposta(_con, _rem, _obj, _iface, _sinal, params):
        codigo, dados = params.unpack()
        resultado["codigo"], resultado["uri"] = codigo, dados.get("uri")
        loop.quit()

    # assina antes de pedir: a resposta pode chegar antes de o pedido voltar
    bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request", "Response",
                         caminho_pedido, None, Gio.DBusSignalFlags.NONE, resposta)
    bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                  "org.freedesktop.portal.Screenshot", "Screenshot",
                  GLib.Variant("(sa{sv})", ("", {"handle_token": GLib.Variant("s", token),
                                                 "interactive": GLib.Variant("b", True)})),
                  None, Gio.DBusCallFlags.NONE, 10000, None)
    GLib.timeout_add_seconds(LIMITE_S, loop.quit)
    loop.run()
    if resultado.get("codigo") != 0 or not resultado.get("uri"):
        return None
    origem = urllib.parse.unquote(urllib.parse.urlparse(resultado["uri"]).path)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    shutil.move(origem, destino)  # o portal salva em ~/Imagens; o print do Jarvis não fica lá
    return destino


if __name__ == "__main__":
    feito = tirar(sys.argv[1] if len(sys.argv) > 1 else DESTINO)
    if not feito:
        print("sem print (cancelado ou sem resposta do portal)", file=sys.stderr)
        sys.exit(1)
    print(feito)
