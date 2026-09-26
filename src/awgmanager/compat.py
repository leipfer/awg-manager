# -*- coding: utf-8 -*-
"""
Совместимость с разными версиями GTK4 / libadwaita.

Ubuntu 24.04 несёт libadwaita 1.5, 26.04 — более свежую, но приложение
может собираться и на старых системах. Отсутствие одного виджета не должно
ронять целое окно: раньше вызов set_subtitle_lines() в конструкторе
приводил к тому, что окно не открывалось вовсе.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


def try_call(obj, method, *args):
    """Вызвать метод, если он существует. Возвращает True при успехе."""
    fn = getattr(obj, method, None)
    if fn is None:
        return False
    try:
        fn(*args)
        return True
    except Exception:
        return False


class CompatSwitchRow(Adw.ActionRow):
    """Замена Adw.SwitchRow для libadwaita < 1.4."""

    def __init__(self, title, subtitle=""):
        super().__init__(title=title, subtitle=subtitle)
        self._sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.add_suffix(self._sw)
        self.set_activatable_widget(self._sw)

    def set_active(self, value):
        self._sw.set_active(bool(value))

    def get_active(self):
        return self._sw.get_active()

    def connect(self, signal, callback, *args):
        """notify::active адресуем внутреннему Gtk.Switch.

        Adw.SwitchRow отдаёт этот сигнал сам; в замене переключатель —
        дочерний виджет, поэтому подписку нужно перенаправить, иначе
        обработчик молча никогда не вызовется.
        """
        if signal == "notify::active":
            return self._sw.connect(signal, lambda *_a: callback(self, None))
        return super().connect(signal, callback, *args)


def switch_row(title, subtitle=""):
    """Adw.SwitchRow там, где он есть, иначе совместимая замена."""
    if hasattr(Adw, "SwitchRow"):
        return Adw.SwitchRow(title=title, subtitle=subtitle)
    return CompatSwitchRow(title, subtitle)


def has(name):
    """Есть ли виджет в текущей версии libadwaita."""
    return hasattr(Adw, name)
