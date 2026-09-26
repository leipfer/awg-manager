# -*- coding: utf-8 -*-
"""AWG Manager — приложение GTK4/libadwaita."""

import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import backend  # noqa: E402
from .prefs import PrefsWindow  # noqa: E402
from .split_ui import SplitWindow  # noqa: E402
from .settings import settings  # noqa: E402
from .window import AwgWindow  # noqa: E402

APP_ID = "org.awgmanager.AwgManager"
VERSION = "1.3.0"


class AwgManagerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.win = None
        self.prefs = None
        self.split_win = None
        self.monitor = None
        self._last = None

    # ------------------------------------------------------------------ #
    def do_startup(self):
        Adw.Application.do_startup(self)
        for name, cb in (
            ("prefs", lambda *_: self.show_prefs()),
            ("split", lambda *_: self.show_split()),
            ("down-all", lambda *_: self.down_all()),
            ("logs", lambda *_: self.show_logs()),
            ("about", lambda *_: self.show_about()),
            ("quit", lambda *_: self.quit_all()),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("app.prefs", ["<Primary>comma"])

    def do_activate(self):
        if not self.win:
            self.win = AwgWindow(self)
            self.win.connect("close-request", self._on_close)
            self.start_monitor()
        self.win.present()

    def _on_close(self, *_):
        self.stop_monitor()
        return False

    # ------------------------------------------------------------------ #
    def start_monitor(self):
        if self.monitor:
            return
        self.monitor = backend.Monitor(
            lambda st: GLib.idle_add(self._on_status, st),
            lambda err: GLib.idle_add(self._on_error, err),
            interval=float(settings.get("interval", 1.0)),
        )
        self.monitor.start()

    def stop_monitor(self):
        if self.monitor:
            self.monitor.stop()
            self.monitor = None

    def restart_monitor(self):
        """Применить новый период опроса."""
        if self.monitor:
            self.stop_monitor()
            self.start_monitor()

    def _on_status(self, st):
        self._last = st
        if self.win:
            self.win.update(st)
        return False

    def _on_error(self, err):
        if self.win:
            self.win.set_error(err)
        return False

    def force_refresh(self):
        self._run_async(
            "*", backend.status, done=lambda r: self._on_status(r) if r else None
        )

    # ------------------------------------------------------------------ #
    def _run_async(self, name, fn, *args, done=None, ok_msg=None):
        if self.win and name != "*":
            self.win.mark_busy(name, True)

        def job():
            result, err = None, None
            try:
                result = fn(*args)
            except backend.HelperError as exc:
                err = str(exc)
            GLib.idle_add(finish, result, err)

        def finish(result, err):
            if self.win:
                if name != "*":
                    self.win.mark_busy(name, False)
                if err:
                    self.win.set_error(err)
                    self.notify("Ошибка", err)
                elif ok_msg:
                    self.win.toast(ok_msg)
                    self.win.set_error(None)
            if err and self.win and name != "*":
                # операция не удалась — вернуть переключатель обратно
                self.force_refresh()
            if done and not err:
                done(result)
            return False

        threading.Thread(target=job, daemon=True).start()

    # ------------------------------------------------------------------ #
    def toggle(self, name, on):
        if on and settings.get("exclusive"):
            others = [i for i in (self._last or {}).get("up", []) if i != name]
            msg = (
                "Туннель {} включён (отключён {})".format(name, ", ".join(others))
                if others
                else "Туннель {} включён".format(name)
            )
            self._run_async(name, backend.switch, name, ok_msg=msg)
        elif on:
            self._run_async(
                name, backend.up, name, ok_msg="Туннель {} включён".format(name)
            )
        else:
            self._run_async(
                name, backend.down, name, ok_msg="Туннель {} выключен".format(name)
            )

    def down_all(self):
        self._run_async("*", backend.down_all, ok_msg="Все туннели отключены")

    def set_autostart(self, name, enabled):
        self._run_async(
            name,
            backend.set_autostart,
            name,
            enabled,
            ok_msg="Автозапуск {} для {}".format(
                "включён" if enabled else "выключен", name
            ),
        )

    def show_prefs(self):
        try:
            if not self.prefs or not self.prefs.get_visible():
                self.prefs = PrefsWindow(self.win)
            self.prefs.present()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            self.prefs = None
            if self.win:
                self.win.set_error("Не удалось открыть настройки: {}".format(exc))

    def show_split(self):
        """Открыть окно раздельного туннелирования.

        Конструктор не должен делать привилегированных вызовов, но если он
        всё же упадёт (например, из-за отсутствующего API в старой
        libadwaita), пользователь обязан увидеть причину, а не зависшее меню.
        """
        try:
            if not self.split_win or not self.split_win.get_visible():
                self.split_win = SplitWindow(self.win, self)
            self.split_win.present()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            self.split_win = None
            if self.win:
                self.win.set_error(
                    "Не удалось открыть раздельное туннелирование: {}".format(exc)
                )
                self.win.toast("Ошибка окна раздельного туннелирования")

    def show_logs(self):
        st = self._last or {}
        up = st.get("up") or [c["name"] for c in st.get("configs", [])][:1]
        if not up:
            if self.win:
                self.win.toast("Нет туннелей для показа журнала")
            return
        self._run_async(
            "*",
            backend.logs,
            up[0],
            120,
            done=lambda text: self.win.show_logs(text) if self.win else None,
        )

    def notify(self, title, body):
        if not settings.get("notifications"):
            return
        try:
            subprocess.Popen(
                ["notify-send", "-a", "AmneziaWG", "-i", "network-vpn-symbolic", title, body]
            )
        except OSError:
            pass

    def show_about(self):
        about = Adw.AboutWindow(
            transient_for=self.win,
            application_name="AmneziaWG Manager",
            application_icon="awg-manager",
            version=VERSION,
            developer_name="AmneziaWG Manager",
            comments=(
                "Графическое управление туннелями AmneziaWG (kernel module).\n"
                "Конфигурации: /etc/amnezia/amneziawg\n"
                "Привилегии выдаются через polkit."
            ),
            license_type=Gtk.License.GPL_3_0,
            
        )
        about.present()

    def show_window(self):
        self.do_activate()

    def quit_all(self):
        self.stop_monitor()
        self.quit()


def main():
    return AwgManagerApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
