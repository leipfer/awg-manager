# -*- coding: utf-8 -*-
"""
Индикатор AmneziaWG в системном трее.

ВАЖНО: AyatanaAppIndicator3 работает только с GTK3, а главное окно —
на GTK4. Смешать их в одном процессе нельзя, поэтому трей запускается
отдельным процессом (`awg-manager-tray`) и общается с главным окном
через DBus-активацию приложения (`gapplication launch`).

Трей самостоятельно держит поток мониторинга через awg-helper.
"""

import os
import shutil
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")

Indicator = None
for _lib, _ver in (("AyatanaAppIndicator3", "0.1"), ("AppIndicator3", "0.1")):
    try:
        gi.require_version(_lib, _ver)
        Indicator = getattr(__import__("gi.repository", fromlist=[_lib]), _lib)
        break
    except (ValueError, ImportError, AttributeError):
        continue

from gi.repository import GLib, Gtk  # noqa: E402

from . import backend  # noqa: E402
from .settings import settings  # noqa: E402

APP_ID = "org.awgmanager.AwgManager"


class TrayApp:
    def __init__(self):
        self.items = {}
        self.busy = set()
        self.monitor = None

        if Indicator is None:
            sys.stderr.write(
                "AppIndicator недоступен. Установите gir1.2-ayatanaappindicator3-0.1\n"
            )
            sys.exit(1)

        self.ind = Indicator.Indicator.new(
            "awg-manager",
            "network-vpn-disabled-symbolic",
            Indicator.IndicatorCategory.SYSTEM_SERVICES,
        )
        self.ind.set_status(Indicator.IndicatorStatus.ACTIVE)
        self.ind.set_title("AmneziaWG")
        self._build([])

    # ------------------------------------------------------------------ #
    def _build(self, configs):
        menu = Gtk.Menu()
        self.items = {}

        self.header = Gtk.MenuItem(label="AmneziaWG: отключено")
        self.header.set_sensitive(False)
        menu.append(self.header)

        self.stats = Gtk.MenuItem(label="—")
        self.stats.set_sensitive(False)
        if settings.get("tray_stats"):
            menu.append(self.stats)

        menu.append(Gtk.SeparatorMenuItem())

        for c in configs:
            item = Gtk.CheckMenuItem(label=c["name"])
            item.set_active(c["up"])
            item._guard = False
            item._name = c["name"]
            item.connect("toggled", self._on_toggled)
            menu.append(item)
            self.items[c["name"]] = item

        if configs:
            menu.append(Gtk.SeparatorMenuItem())

        for label, cb in (
            ("Отключить все", self.down_all),
            ("Открыть окно", self.open_window),
        ):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _w, f=cb: f())
            menu.append(mi)

        menu.append(Gtk.SeparatorMenuItem())

        mi = Gtk.MenuItem(label="Выход (только значок)")
        mi.connect("activate", lambda *_: self.quit())
        menu.append(mi)

        menu.show_all()
        self.menu = menu
        self.ind.set_menu(menu)

    # ------------------------------------------------------------------ #
    def _on_toggled(self, item):
        if getattr(item, "_guard", False) or item._name in self.busy:
            return
        self.toggle(item._name, item.get_active())

    def _work(self, name, fn, *a):
        """Выполнить привилегированную операцию в отдельном потоке."""
        import threading

        self.busy.add(name)

        def job():
            err = None
            try:
                settings.load()
                fn(*a)
            except backend.HelperError as exc:
                err = str(exc)
            GLib.idle_add(self._done, name, err)

        threading.Thread(target=job, daemon=True).start()

    def _done(self, name, err):
        self.busy.discard(name)
        if err:
            self.notify("AmneziaWG", err)
        return False

    def toggle(self, name, on):
        if on and settings.get("exclusive"):
            self._work(name, backend.switch, name)
        else:
            self._work(name, backend.up if on else backend.down, name)

    def down_all(self):
        self._work("*", backend.down_all)

    def open_window(self):
        exe = shutil.which("awg-manager")
        if exe:
            subprocess.Popen([exe])
        else:
            subprocess.Popen(["gapplication", "launch", APP_ID])

    def notify(self, title, body):
        if not settings.get("notifications"):
            return
        try:
            subprocess.Popen(
                ["notify-send", "-a", "AmneziaWG", "-i", "network-vpn-symbolic", title, body]
            )
        except OSError:
            pass

    def quit(self):
        if self.monitor:
            self.monitor.stop()
        Gtk.main_quit()

    # ------------------------------------------------------------------ #
    def update(self, st):
        configs = st.get("configs", [])
        if set(self.items) != {c["name"] for c in configs}:
            self._build(configs)

        up = st.get("up", [])
        ifaces = st.get("interfaces", {})
        now = st.get("ts", 0)

        for c in configs:
            item = self.items.get(c["name"])
            if not item:
                continue
            item._guard = True
            item.set_active(c["up"])
            item._guard = False

        if up:
            name = up[0]
            peers = ifaces.get(name, {}).get("peers", [])
            hs = max((p.get("latest_handshake", 0) for p in peers), default=0)
            rx = sum(p.get("rx", 0) for p in peers)
            tx = sum(p.get("tx", 0) for p in peers)
            fresh = hs and (now - hs) < 190
            self.ind.set_icon_full(
                "network-vpn-symbolic" if fresh else "network-vpn-acquiring-symbolic",
                "AmneziaWG подключён",
            )
            self.header.set_label(
                "AmneziaWG: {} ({})".format(name, "подключён" if fresh else "подключение…")
            )
            self.stats.set_label(
                "↓ {}   ↑ {}   ⟳ {}".format(
                    backend.human_bytes(rx),
                    backend.human_bytes(tx),
                    backend.human_ago(hs, now),
                )
            )
        else:
            self.ind.set_icon_full("network-vpn-disabled-symbolic", "AmneziaWG отключён")
            self.header.set_label("AmneziaWG: отключено")
            self.stats.set_label("—")

    def on_status(self, st):
        GLib.idle_add(self.update, st)

    def on_error(self, msg):
        GLib.idle_add(self.header.set_label, "AmneziaWG: ошибка связи")

    def run(self):
        interval = max(1.0, float(settings.get("interval", 1.0)))
        self.monitor = backend.Monitor(self.on_status, self.on_error, interval=interval)
        self.monitor.start()
        Gtk.main()


def main():
    # значок бесполезен без графической сессии
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        sys.stderr.write("Нет графической сессии.\n")
        return 1
    TrayApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
