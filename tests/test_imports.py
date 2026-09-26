# -*- coding: utf-8 -*-
"""
Регрессия: NameError: name 'MODE_LABELS' is not defined.

Константы были случайно удалены при вырезании блока кода из файла.
Ни компиляция, ни существовавшие тесты этого не замечали: ошибка
возникала только в рантайме при открытии окна.

Здесь модули GUI импортируются с заглушкой gi — так отсутствие
глобального имени обнаруживается сразу, без графической сессии.
"""

import ast
import builtins
import importlib
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)


def install_gi_stub():
    """Заглушка gi: позволяет импортировать GUI-модули без GTK."""

    class Any:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, n):
            return Any()

        def __call__(self, *a, **k):
            return Any()

    class Meta(type):
        def __getattr__(cls, n):
            return Any()

    class Base(metaclass=Meta):
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, n):
            return Any()

    class NS:
        def __getattr__(self, n):
            return Base

    gi = types.ModuleType("gi")
    gi.require_version = lambda *a, **k: None
    repo = types.ModuleType("gi.repository")

    adw, gtk, gio, pango = NS(), NS(), NS(), NS()
    glib = types.SimpleNamespace(idle_add=lambda *a, **k: None, Error=Exception)
    gdk = types.SimpleNamespace(
        Display=types.SimpleNamespace(get_default=lambda: None)
    )
    repo.Adw, repo.Gtk, repo.Gio = adw, gtk, gio
    repo.GLib, repo.Pango, repo.Gdk = glib, pango, gdk
    gi.repository = repo

    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo
    for name, val in (("Adw", adw), ("Gtk", gtk), ("Gio", gio),
                      ("GLib", glib), ("Pango", pango), ("Gdk", gdk)):
        sys.modules["gi.repository." + name] = val


class ModuleImportTest(unittest.TestCase):
    """Каждый модуль обязан импортироваться без NameError."""

    MODULES = [
        "awgmanager.settings",
        "awgmanager.presets",
        "awgmanager.backend",
        "awgmanager.compat",
        "awgmanager.split_ui",
    ]

    @classmethod
    def setUpClass(cls):
        install_gi_stub()

    def test_modules_import(self):
        for name in self.MODULES:
            sys.modules.pop(name, None)
            with self.subTest(module=name):
                importlib.import_module(name)


class ModuleGlobalsTest(unittest.TestCase):
    """
    Статическая проверка: имя, используемое на уровне модуля или
    в теле функции, должно быть где-то определено.
    """

    SRC_DIR = os.path.join(SRC, "awgmanager")

    def _undefined_names(self, path):
        tree = ast.parse(open(path, encoding="utf-8").read())
        known = set(dir(builtins)) | {"self", "cls", "__file__", "__name__", "__doc__"}

        # всё, что связывается где угодно в модуле
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                known.add(node.name)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = node.args
                    for arg in (list(a.args) + list(a.kwonlyargs)
                                + list(a.posonlyargs)):
                        known.add(arg.arg)
                    if a.vararg:
                        known.add(a.vararg.arg)
                    if a.kwarg:
                        known.add(a.kwarg.arg)
            elif isinstance(node, ast.Lambda):
                a = node.args
                for arg in (list(a.args) + list(a.kwonlyargs) + list(a.posonlyargs)):
                    known.add(arg.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    known.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ExceptHandler) and node.name:
                known.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign,
                                   ast.For, ast.comprehension, ast.withitem)):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target]
                elif isinstance(node, ast.For):
                    targets = [node.target]
                elif isinstance(node, ast.comprehension):
                    targets = [node.target]
                elif isinstance(node, ast.withitem):
                    targets = [node.optional_vars] if node.optional_vars else []
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            known.add(n.id)
            elif isinstance(node, ast.Global):
                known.update(node.names)

        missing = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in known:
                    missing.setdefault(node.id, node.lineno)
        return missing

    def test_no_undefined_globals(self):
        for fname in sorted(os.listdir(self.SRC_DIR)):
            if not fname.endswith(".py"):
                continue
            with self.subTest(file=fname):
                missing = self._undefined_names(os.path.join(self.SRC_DIR, fname))
                self.assertEqual(
                    missing, {},
                    "неопределённые имена в {}: {}".format(fname, missing),
                )


class SplitModeConstantsTest(unittest.TestCase):
    """MODES / MODE_LABELS / MODE_HINTS должны существовать и совпадать."""

    @classmethod
    def setUpClass(cls):
        install_gi_stub()
        sys.modules.pop("awgmanager.split_ui", None)
        cls.su = importlib.import_module("awgmanager.split_ui")

    def test_constants_exist(self):
        for name in ("MODES", "MODE_LABELS", "MODE_HINTS"):
            self.assertTrue(hasattr(self.su, name), "нет константы " + name)

    def test_same_length(self):
        self.assertEqual(len(self.su.MODES), 3)
        self.assertEqual(len(self.su.MODE_LABELS), len(self.su.MODES))
        self.assertEqual(len(self.su.MODE_HINTS), len(self.su.MODES))

    def test_modes_values(self):
        self.assertEqual(self.su.MODES, ["off", "include", "exclude"])

    def test_labels_are_short(self):
        """Длинные подписи обрезаются в GtkDropDown многоточием."""
        for label in self.su.MODE_LABELS:
            self.assertLessEqual(len(label), 20, "слишком длинная подпись: " + label)


if __name__ == "__main__":
    unittest.main(verbosity=2)
