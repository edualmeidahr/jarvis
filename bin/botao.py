#!/usr/bin/env python3
"""Notificação do GNOME com botão "Entrar na chamada" que abre o link.

    botao.py TITULO CORPO [LINK]

Existe porque o notify-send 0.8.8 desta máquina recusa botão ("Actions are not
supported"), embora o gnome-shell anuncie `actions` no GetCapabilities. Aqui
a conversa com o servidor é direta, pelo D-Bus.

Um processo só cria a notificação e escuta o clique: o GNOME devolve o
ActionInvoked para a conexão que chamou o Notify. Se o processo saísse logo
depois de criar, o clique não teria para quem voltar.

Termina sozinho quando você clica, fecha a notificação, ou em 2 horas.
"""
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

NOME = "org.freedesktop.Notifications"
CAMINHO = "/org/freedesktop/Notifications"
LIMITE_S = 2 * 60 * 60


def main():
    if len(sys.argv) < 3:
        sys.exit("uso: botao.py TITULO CORPO [LINK]")
    titulo, corpo = sys.argv[1], sys.argv[2]
    link = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None

    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    acoes = ["entrar", "Entrar na chamada"] if link else []
    dicas = {"urgency": GLib.Variant("y", 2)}  # 2 = crítica: fica até você fechar
    resp = bus.call_sync(NOME, CAMINHO, NOME, "Notify",
                         GLib.Variant("(susssasa{sv}i)",
                                      ("Jarvis", 0, "appointment-soon", titulo, corpo, acoes, dicas, -1)),
                         GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None)
    meu_id = resp.unpack()[0]
    if not link:
        return 0  # sem botão, não há clique para esperar

    laco = GLib.MainLoop()

    def sinal(_con, _rem, _cam, _iface, nome, params):
        valores = params.unpack()
        if valores[0] != meu_id:
            return
        if nome == "ActionInvoked" and valores[1] == "entrar":
            Gio.AppInfo.launch_default_for_uri(link, None)
            print("clicou em entrar", file=sys.stderr)
        else:
            print(f"fechou sem entrar ({nome})", file=sys.stderr)
        laco.quit()

    for s in ("ActionInvoked", "NotificationClosed"):
        bus.signal_subscribe(NOME, NOME, s, CAMINHO, None, Gio.DBusSignalFlags.NONE, sinal)
    GLib.timeout_add_seconds(LIMITE_S, laco.quit)
    laco.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
