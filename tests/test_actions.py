# -*- coding: utf-8 -*-
"""
Регрессия: 'SplitWindow' object has no attribute 'add_action'.

Adw.Window наследуется от Gtk.Window, который НЕ реализует GActionMap —
add_action() есть только у Gtk.ApplicationWindow. Раньше это обнаруживалось
лишь в рантайме при попытке открыть окно.

Тесты работают со статическим анализом исходников: GTK в CI нет.
"""

import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "awgmanager")

# Классы, реализующие GActionMap (можно вызывать self.add_action)
ACTION_MAP_BASES = {"Gtk.ApplicationWindow", "Adw.Application",
                    "Gtk.Application", "Adw.ApplicationWindow"}


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as fh:
        return fh.read()


def classes_with_bases(source):
    tree = ast.parse(source)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                bases.append(ast.unparse(b) if hasattr(ast, "unparse") else "")
            out[node.name] = (node, bases)
    return out


class ActionMapTest(unittest.TestCase):
    """self.add_action() допустим только там, где есть GActionMap."""

    def test_no_add_action_on_plain_windows(self):
        offenders = []
        for fname in os.listdir(SRC):
            if not fname.endswith(".py"):
                continue
            src = read(fname)
            for cname, (node, bases) in classes_with_bases(src).items():
                if any(b in ACTION_MAP_BASES for b in bases):
                    continue
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr == "add_action"
                            and isinstance(sub.func.value, ast.Name)
                            and sub.func.value.id == "self"):
                        offenders.append(
                            "{}:{} класс {} (базы: {})".format(
                                fname, sub.lineno, cname, ", ".join(bases))
                        )
        self.assertEqual(
            offenders, [],
            "self.add_action() в классе без GActionMap:\n" + "\n".join(offenders),
        )

    def test_split_window_uses_action_group(self):
        src = read("split_ui.py")
        self.assertIn("Gio.SimpleActionGroup()", src)
        self.assertIn('insert_action_group("win"', src)


class MenuActionsResolveTest(unittest.TestCase):
    """Каждый пункт меню должен ссылаться на существующее действие."""

    def _menu_targets(self, source, prefix):
        return re.findall(r'menu\.append\([^,]+,\s*"' + prefix + r'\.([\w-]+)"\)',
                          source)

    def test_window_menu_actions_exist_in_app(self):
        targets = self._menu_targets(read("window.py"), "app")
        app_src = read("app.py")
        declared = set(re.findall(r'\("([\w-]+)",\s*lambda', app_src))
        missing = [t for t in targets if t not in declared]
        self.assertEqual(missing, [],
                         "меню ссылается на несуществующие app-действия: %s" % missing)
        self.assertIn("split", targets)

    def test_split_menu_actions_are_registered(self):
        src = read("split_ui.py")
        targets = self._menu_targets(src, "win")
        registered = set(re.findall(r'\("([\w-]+)",\s*self\._on_\w+\)', src))
        missing = [t for t in targets if t not in registered]
        self.assertEqual(missing, [],
                         "меню win.* без обработчика: %s" % missing)
        self.assertTrue(targets, "не найдено ни одного пункта меню")

    def test_split_handlers_exist(self):
        src = read("split_ui.py")
        for name in ("_on_save_set", "_on_import", "_on_export", "_on_verify"):
            self.assertIn("def {}(".format(name), src,
                          "нет обработчика " + name)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class SetsUiTest(unittest.TestCase):
    """Создание набора должно быть видимым действием, а не только пунктом меню."""

    def test_create_button_exists_in_sets_group(self):
        src = read("split_ui.py")
        block = src[src.index("def _rebuild_sets"): src.index("def _delete_set")]
        self.assertIn("Создать свой набор", block,
                      "в группе «Наборы» нет строки создания набора")
        self.assertIn("self._on_save_set()", block,
                      "кнопка создания не вызывает обработчик")

    def test_edit_and_delete_available_for_user_sets(self):
        src = read("split_ui.py")
        block = src[src.index("def _rebuild_sets"): src.index("def _delete_set")]
        self.assertIn("_on_edit_set", block)
        self.assertIn("_confirm_delete", block)

    def test_delete_is_confirmed(self):
        src = read("split_ui.py")
        self.assertIn("def _confirm_delete", src)
        block = src[src.index("def _confirm_delete"):]
        self.assertIn("DESTRUCTIVE", block)

    def test_handlers_defined(self):
        src = read("split_ui.py")
        for name in ("_on_save_set", "_on_edit_set", "_confirm_delete",
                     "_delete_set", "_set_dialog"):
            self.assertIn("def {}(".format(name), src, "нет метода " + name)
