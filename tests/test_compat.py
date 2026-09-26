# -*- coding: utf-8 -*-
"""
Регрессия на баг: окно раздельного туннелирования не открывалось.

Причина — вызов API отсутствующего в текущей libadwaita прямо в
конструкторе: исключение убивало создание окна до present().
"""

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def install_gi_stub(with_switchrow, with_subtitle_lines):
    """Подменить gi заглушкой, имитируя конкретную версию libadwaita."""

    class Switch:
        def __init__(self, *a, **k):
            self._handlers = []
            self._v = False

        def connect(self, sig, cb):
            self._handlers.append(cb)
            return 1

        def set_active(self, v):
            self._v = bool(v)
            for h in self._handlers:
                h(self, None)

        def get_active(self):
            return self._v

    class ActionRow:
        def __init__(self, *a, **k):
            pass

        def add_suffix(self, w):
            pass

        def set_activatable_widget(self, w):
            pass

        def connect(self, *a):
            return 0

        if with_subtitle_lines:
            def set_subtitle_lines(self, n):
                self.lines = n

    adw = types.SimpleNamespace(ActionRow=ActionRow)
    if with_switchrow:
        class SwitchRow:
            def __init__(self, title="", subtitle=""):
                self._v = False

            def set_active(self, v):
                self._v = v

            def get_active(self):
                return self._v

        adw.SwitchRow = SwitchRow

    gi = types.ModuleType("gi")
    gi.require_version = lambda *a, **k: None
    repo = types.ModuleType("gi.repository")
    repo.Adw = adw
    repo.Gtk = types.SimpleNamespace(
        Switch=Switch, Align=types.SimpleNamespace(CENTER=1)
    )
    gi.repository = repo
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo
    sys.modules["gi.repository.Adw"] = adw
    sys.modules["gi.repository.Gtk"] = repo.Gtk
    # Сбрасываем и сам модуль, и ссылку-атрибут в пакете: иначе
    # `from awgmanager import compat` вернёт версию, импортированную
    # с предыдущей заглушкой, и тест проверит не то, что нужно.
    sys.modules.pop("awgmanager.compat", None)
    pkg = sys.modules.get("awgmanager")
    if pkg is not None and hasattr(pkg, "compat"):
        delattr(pkg, "compat")

    import importlib

    return importlib.import_module("awgmanager.compat")


class CompatTest(unittest.TestCase):
    def tearDown(self):
        for m in ("gi", "gi.repository", "gi.repository.Adw",
                  "gi.repository.Gtk", "awgmanager.compat"):
            sys.modules.pop(m, None)
        pkg = sys.modules.get("awgmanager")
        if pkg is not None and hasattr(pkg, "compat"):
            delattr(pkg, "compat")

    def test_switch_row_uses_native_when_available(self):
        c = install_gi_stub(with_switchrow=True, with_subtitle_lines=True)
        self.assertEqual(type(c.switch_row("t")).__name__, "SwitchRow")

    def test_switch_row_falls_back_on_old_libadwaita(self):
        c = install_gi_stub(with_switchrow=False, with_subtitle_lines=False)
        row = c.switch_row("t", "s")
        self.assertEqual(type(row).__name__, "CompatSwitchRow")
        row.set_active(True)
        self.assertTrue(row.get_active())

    def test_fallback_forwards_notify_active(self):
        """Без проброса сигнала автозапуск молча перестал бы работать."""
        c = install_gi_stub(with_switchrow=False, with_subtitle_lines=False)
        row = c.switch_row("t")
        fired = []
        row.connect("notify::active", lambda r, _p: fired.append(r.get_active()))
        row.set_active(True)
        row.set_active(False)
        self.assertEqual(fired, [True, False])

    def test_try_call_missing_method_is_safe(self):
        c = install_gi_stub(with_switchrow=False, with_subtitle_lines=False)
        row = c.switch_row("t")
        self.assertFalse(c.try_call(row, "set_subtitle_lines", 2))

    def test_try_call_existing_method(self):
        c = install_gi_stub(with_switchrow=True, with_subtitle_lines=True)

        class Obj:
            def set_subtitle_lines(self, n):
                self.n = n

        o = Obj()
        self.assertTrue(c.try_call(o, "set_subtitle_lines", 3))
        self.assertEqual(o.n, 3)

    def test_has(self):
        c = install_gi_stub(with_switchrow=True, with_subtitle_lines=True)
        self.assertTrue(c.has("SwitchRow"))
        self.assertFalse(c.has("НетТакогоВиджета"))


class NoBlockingInConstructorTest(unittest.TestCase):
    """Конструктор окна не должен делать привилегированных вызовов."""

    def test_split_window_ctor_has_no_backend_calls(self):
        path = os.path.join(ROOT, "src", "awgmanager", "split_ui.py")
        src = open(path, encoding="utf-8").read()
        ctor = src[src.index("class SplitWindow"): src.index("def _deferred_init")]
        for bad in ("backend.split_show", "backend.split_apply", "self._load()"):
            self.assertNotIn(bad, ctor, "блокирующий вызов в конструкторе: " + bad)

    def test_deferred_init_scheduled(self):
        path = os.path.join(ROOT, "src", "awgmanager", "split_ui.py")
        src = open(path, encoding="utf-8").read()
        self.assertIn("GLib.idle_add(self._deferred_init)", src)

    def test_show_split_is_guarded(self):
        path = os.path.join(ROOT, "src", "awgmanager", "app.py")
        src = open(path, encoding="utf-8").read()
        block = src[src.index("def show_split"): src.index("def show_logs")]
        self.assertIn("try:", block)
        self.assertIn("except Exception", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
