# -*- coding: utf-8 -*-
"""Окно раздельного туннелирования: подсети, домены и наборы."""

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import backend, presets  # noqa: E402
from .compat import switch_row as _switch_row, try_call as _try  # noqa: E402

MODES = ["off", "include", "exclude"]

# Короткие подписи — GtkDropDown не переносит строки и обрезает длинный
# текст многоточием. Полное пояснение выводится отдельной строкой.
MODE_LABELS = ["Выключено", "Только список", "Кроме списка"]

MODE_HINTS = [
    "Весь трафик идёт через VPN. Список не применяется.",
    "Через VPN пойдут ТОЛЬКО перечисленные адреса и домены. "
    "Всё остальное — напрямую.",
    "Перечисленные адреса и домены пойдут НАПРЯМУЮ, мимо VPN. "
    "Всё остальное — через VPN. Обычно так добавляют банки и госуслуги.",
]


class SplitWindow(Adw.Window):
    def __init__(self, parent, app):
        super().__init__(transient_for=parent, modal=False)
        self.app = app
        self.set_title("Раздельное туннелирование")
        self.set_default_size(680, 820)

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.apply_btn = Gtk.Button(label="Применить")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", self._on_apply)
        header.pack_end(self.apply_btn)

        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)

        menu = Gio.Menu()
        menu.append("Импорт из файла…", "win.import")
        menu.append("Экспорт в файл…", "win.export")
        menu.append("Проверить маршрут…", "win.verify")
        header.pack_start(Gtk.MenuButton(icon_name="open-menu-symbolic",
                                         menu_model=menu))
        tv.add_top_bar(header)

        # Adw.Window наследуется от Gtk.Window, а тот НЕ реализует GActionMap
        # (это умеет только Gtk.ApplicationWindow). Поэтому self.add_action()
        # здесь недоступен — действия кладём в отдельную группу и вставляем
        # её под префиксом "win", на который ссылается меню.
        actions = Gio.SimpleActionGroup()
        for name, cb in (
            ("save-set", self._on_save_set),
            ("import", self._on_import),
            ("export", self._on_export),
            ("verify", self._on_verify),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, f=cb: f())
            actions.add_action(act)
        self.insert_action_group("win", actions)
        self._actions = actions

        self.toasts = Adw.ToastOverlay()
        tv.set_content(self.toasts)
        self.set_content(tv)

        page = Adw.PreferencesPage()
        self.toasts.set_child(page)

        # ---------------- режим ----------------
        g = Adw.PreferencesGroup(title="Режим")
        self.mode_row = Adw.ComboRow(title="Режим работы")
        model = Gtk.StringList()
        for label in MODE_LABELS:
            model.append(label)
        self.mode_row.set_model(model)
        self.mode_row.connect("notify::selected", lambda *_: self._update_hint())
        g.add(self.mode_row)

        # Пояснение отдельной строкой с переносом — в выпадающем
        # списке длинный текст обрезается многоточием.
        self.hint = Gtk.Label(xalign=0)
        self.hint.set_wrap(True)
        self.hint.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.hint.add_css_class("dim-label")
        self.hint.set_margin_top(6)
        self.hint.set_margin_bottom(6)
        self.hint.set_margin_start(12)
        self.hint.set_margin_end(12)
        g.add(self.hint)

        self.auto_row = _switch_row(
            "Применять автоматически",
            "Восстанавливать правила при каждом подключении туннеля",
        )
        self.auto_row.set_active(True)
        g.add(self.auto_row)
        page.add(g)

        # ---------------- список ----------------
        g2 = Adw.PreferencesGroup(
            title="Адреса и домены",
            description="По одной записи на строку: 192.168.0.0/16, 8.8.8.8 "
                        "или chatgpt.com / https://chatgpt.com",
        )
        self.text = Gtk.TextView(monospace=True)
        self.text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for side in ("top", "bottom", "start", "end"):
            getattr(self.text, "set_margin_" + side)(8)
        self.text.get_buffer().connect("changed", lambda *_: self._update_counter())
        sc = Gtk.ScrolledWindow()
        sc.set_min_content_height(240)
        sc.set_child(self.text)
        frame = Gtk.Frame()
        frame.set_child(sc)
        g2.add(frame)

        self.counter = Gtk.Label(xalign=0)
        self.counter.add_css_class("dim-label")
        self.counter.set_margin_top(6)
        self.counter.set_margin_start(4)
        g2.add(self.counter)
        page.add(g2)

        # ---------------- наборы ----------------
        self.sets_group = Adw.PreferencesGroup(
            title="Наборы",
            description="«Добавить» — перенести записи набора в список выше. "
                        "«Создать» — сохранить текущий список как свой набор "
                        "(импорт и экспорт — в меню окна).",
        )
        page.add(self.sets_group)

        # ---------------- состояние ----------------
        g4 = Adw.PreferencesGroup(title="Состояние")
        self.state_row = Adw.ActionRow(title="Статус", subtitle="загрузка…")
        _try(self.state_row, "set_subtitle_lines", 3)
        g4.add(self.state_row)
        page.add(g4)

        page.add(Adw.PreferencesGroup(description=(
            "Домены преобразуются в IP-адреса при каждом применении и при "
            "подключении туннеля, поэтому остаются актуальными.\n\n"
            "Крупные сайты используют CDN и десятки адресов — если сайт "
            "работает не так, как ожидается, добавьте его подсети вручную "
            "или воспользуйтесь пунктом «Проверить маршрут»."
        )))

        self._update_hint()
        self._update_counter()

        # Наборы и состояние подгружаем ПОСЛЕ отрисовки окна: конструктор
        # должен отработать мгновенно, иначе окно не успеет появиться.
        GLib.idle_add(self._deferred_init)

    def _deferred_init(self):
        try:
            self._rebuild_sets()
        except Exception as exc:
            self._toast("Не удалось построить список наборов: {}".format(exc))
        self._load()
        return False

    # ------------------------------------------------------------------ #
    def _update_hint(self):
        self.hint.set_text(MODE_HINTS[self.mode_row.get_selected()])

    def _get_text(self):
        buf = self.text.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _set_text(self, entries):
        self.text.get_buffer().set_text("\n".join(entries) + ("\n" if entries else ""))

    def _update_counter(self):
        entries, bad = presets.parse_entries(self._get_text())
        nets = sum(1 for e in entries if presets.classify_entry(e)[0] == "net")
        doms = len(entries) - nets
        text = "подсетей: {} · доменов: {}".format(nets, doms)
        if bad:
            text += " · некорректных: {} ({})".format(len(bad), ", ".join(bad[:2]))
        self.counter.set_text(text)

    def _add(self, entries):
        cur, _bad = presets.parse_entries(self._get_text())
        for e in entries:
            if e not in cur:
                cur.append(e)
        self._set_text(cur)

    def _rebuild_sets(self):
        for row in getattr(self, "_set_rows", []):
            self.sets_group.remove(row)
        self._set_rows = []

        # Создание набора — первой строкой и заметной кнопкой: раньше это
        # действие пряталось в бургер-меню, и его никто не находил.
        create = Adw.ActionRow(
            title="Создать свой набор",
            subtitle="Сохранить текущий список как набор",
        )
        _try(create, "set_subtitle_lines", 2)
        icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
        create.add_prefix(icon)
        cbtn = Gtk.Button(label="Создать", valign=Gtk.Align.CENTER)
        cbtn.add_css_class("suggested-action")
        cbtn.connect("clicked", lambda _b: self._on_save_set())
        create.add_suffix(cbtn)
        create.set_activatable_widget(cbtn)
        self.sets_group.add(create)
        self._set_rows.append(create)

        user_sets = [i for i in presets.all_sets() if not i["builtin"]]
        builtin_sets = [i for i in presets.all_sets() if i["builtin"]]

        for item in user_sets + builtin_sets:
            row = Adw.ActionRow(title=item["name"])
            sub = item.get("description") or ""
            count = "{} записей".format(len(item["entries"]))
            tag = "" if item["builtin"] else "свой · "
            row.set_subtitle("{}{} · {}".format(tag, sub, count)
                             if sub else "{}{}".format(tag, count))
            _try(row, "set_subtitle_lines", 2)

            btn = Gtk.Button(label="Добавить", valign=Gtk.Align.CENTER)
            btn.set_tooltip_text("Добавить записи набора в список")
            btn.connect("clicked", lambda _b, e=item["entries"]: self._add(e))
            row.add_suffix(btn)

            if not item["builtin"]:
                edit = Gtk.Button(icon_name="document-edit-symbolic",
                                  valign=Gtk.Align.CENTER)
                edit.add_css_class("flat")
                edit.set_tooltip_text("Переименовать или перезаписать набор")
                edit.connect("clicked", lambda _b, i=item: self._on_edit_set(i))
                row.add_suffix(edit)

                dele = Gtk.Button(icon_name="user-trash-symbolic",
                                  valign=Gtk.Align.CENTER)
                dele.add_css_class("flat")
                dele.set_tooltip_text("Удалить набор")
                dele.connect("clicked", lambda _b, n=item["name"]: self._confirm_delete(n))
                row.add_suffix(dele)

            self.sets_group.add(row)
            self._set_rows.append(row)

        clear = Adw.ActionRow(title="Очистить список")
        btn = Gtk.Button(label="Очистить", valign=Gtk.Align.CENTER)
        btn.add_css_class("destructive-action")
        btn.connect("clicked", lambda _b: self.text.get_buffer().set_text(""))
        clear.add_suffix(btn)
        self.sets_group.add(clear)
        self._set_rows.append(clear)

    def _delete_set(self, name):
        presets.delete_set(name)
        self._rebuild_sets()
        self.toasts.add_toast(Adw.Toast.new("Набор «{}» удалён".format(name)))

    # ------------------------------------------------------------------ #
    def _set_dialog(self, heading, body, init_name="", init_desc="",
                    original=None, entries=None):
        """Общий диалог создания и редактирования набора."""
        dlg = Adw.MessageDialog(transient_for=self, heading=heading, body=body)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        name = Gtk.Entry(placeholder_text="Название набора")
        name.set_text(init_name)
        desc = Gtk.Entry(placeholder_text="Описание (необязательно)")
        desc.set_text(init_desc)
        box.append(name)
        box.append(desc)

        replace = None
        if original is None:
            replace = Gtk.CheckButton(
                label="Перезаписать, если набор с таким именем существует")
            box.append(replace)

        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Отмена")
        dlg.add_response("save", "Сохранить")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def done(_d, resp):
            if resp != "save":
                return
            new_name = name.get_text().strip()
            try:
                if original and original != new_name:
                    presets.delete_set(original)
                presets.save_set(
                    new_name, entries, desc.get_text().strip(),
                    overwrite=bool(original) or (replace and replace.get_active()),
                )
            except ValueError as exc:
                self._toast(str(exc))
                return
            self._rebuild_sets()
            self._toast("Набор «{}» сохранён".format(new_name))

        dlg.connect("response", done)
        dlg.present()

    def _on_save_set(self):
        entries, bad = presets.parse_entries(self._get_text())
        if not entries:
            self._toast("Сначала добавьте адреса или домены в список")
            return
        if bad:
            self._toast("Исправьте некорректные записи: " + ", ".join(bad[:3]))
            return
        self._set_dialog(
            "Создать набор",
            "Текущий список ({} записей) будет сохранён как ваш набор "
            "и появится в списке ниже.".format(len(entries)),
            entries=entries,
        )

    def _on_edit_set(self, item):
        """Переименовать набор и/или заменить его содержимое текущим списком."""
        current, bad = presets.parse_entries(self._get_text())

        dlg = Adw.MessageDialog(
            transient_for=self,
            heading="Изменить набор «{}»".format(item["name"]),
            body="Можно переименовать набор или заменить его содержимое "
                 "текущим списком ({} записей).".format(len(current)),
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        name = Gtk.Entry(placeholder_text="Название набора")
        name.set_text(item["name"])
        desc = Gtk.Entry(placeholder_text="Описание (необязательно)")
        desc.set_text(item.get("description", ""))
        box.append(name)
        box.append(desc)

        replace = Gtk.CheckButton(
            label="Заменить содержимое текущим списком ({} записей)".format(
                len(current)))
        replace.set_sensitive(bool(current))
        box.append(replace)
        dlg.set_extra_child(box)

        dlg.add_response("cancel", "Отмена")
        dlg.add_response("save", "Сохранить")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def done(_d, resp):
            if resp != "save":
                return
            new_name = name.get_text().strip()
            entries = current if (replace.get_active() and current) else item["entries"]
            try:
                if new_name != item["name"]:
                    presets.delete_set(item["name"])
                presets.save_set(new_name, entries, desc.get_text().strip(),
                                 overwrite=True)
            except ValueError as exc:
                self._toast(str(exc))
                return
            self._rebuild_sets()
            self._toast("Набор «{}» обновлён".format(new_name))

        dlg.connect("response", done)
        dlg.present()

    def _confirm_delete(self, name):
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading="Удалить набор «{}»?".format(name),
            body="Действие нельзя отменить. Записи в текущем списке останутся.",
        )
        dlg.add_response("cancel", "Отмена")
        dlg.add_response("delete", "Удалить")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: self._delete_set(name)
                    if r == "delete" else None)
        dlg.present()

    def _on_import(self):
        dialog = Gtk.FileDialog(title="Импорт набора")
        dialog.open(self, None, self._import_done)

    def _import_done(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        try:
            with open(gfile.get_path(), "r", errors="replace") as fh:
                entries, bad = presets.import_text(fh.read())
        except OSError as exc:
            self.toasts.add_toast(Adw.Toast.new("Ошибка чтения: " + str(exc)))
            return
        self._add(entries)
        msg = "Импортировано записей: {}".format(len(entries))
        if bad:
            msg += ", пропущено: {}".format(len(bad))
        self.toasts.add_toast(Adw.Toast.new(msg))

    def _on_export(self):
        entries, _bad = presets.parse_entries(self._get_text())
        if not entries:
            self.toasts.add_toast(Adw.Toast.new("Список пуст"))
            return
        dialog = Gtk.FileDialog(title="Экспорт набора",
                                initial_name="awg-split-set.txt")
        dialog.save(self, None, lambda d, r: self._export_done(d, r, entries))

    def _export_done(self, dialog, result, entries):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        try:
            with open(gfile.get_path(), "w") as fh:
                fh.write(presets.export_text(entries))
        except OSError as exc:
            self.toasts.add_toast(Adw.Toast.new("Ошибка записи: " + str(exc)))
            return
        self.toasts.add_toast(Adw.Toast.new("Сохранено"))

    def _on_verify(self):
        dlg = Adw.MessageDialog(
            transient_for=self, heading="Проверить маршрут",
            body="Куда пойдёт трафик к указанному адресу или домену?",
        )
        entry = Gtk.Entry(placeholder_text="chatgpt.com или 8.8.8.8")
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Отмена")
        dlg.add_response("check", "Проверить")
        dlg.set_response_appearance("check", Adw.ResponseAppearance.SUGGESTED)

        def done(_d, resp):
            if resp != "check" or not entry.get_text().strip():
                return
            target = entry.get_text().strip()

            def job():
                try:
                    data = backend.split_verify(target)
                except backend.HelperError as exc:
                    GLib.idle_add(self._toast, "Ошибка: " + str(exc))
                    return
                GLib.idle_add(self._show_verify, target, data)

            threading.Thread(target=job, daemon=True).start()

        dlg.connect("response", done)
        dlg.present()

    def _show_verify(self, target, data):
        lines = []
        for r in data.get("results", []):
            lines.append("{} → {} ({})".format(
                r["ip"], r.get("device") or "?",
                "через VPN" if r.get("via_vpn") else "напрямую"))
        dlg = Adw.MessageDialog(
            transient_for=self, heading="Маршрут для " + target,
            body="\n".join(lines) or "не удалось определить",
        )
        dlg.add_response("ok", "Понятно")
        dlg.present()
        return False

    def _toast(self, text):
        self.toasts.add_toast(Adw.Toast.new(text))
        return False

    # ------------------------------------------------------------------ #
    def _load(self):
        def job():
            try:
                data = backend.split_show()
            except backend.HelperError as exc:
                GLib.idle_add(self.state_row.set_subtitle, "Ошибка: " + str(exc))
                return
            GLib.idle_add(self._fill, data)

        threading.Thread(target=job, daemon=True).start()

    def _fill(self, data):
        mode = data.get("mode", "off")
        self.mode_row.set_selected(MODES.index(mode) if mode in MODES else 0)
        self.auto_row.set_active(bool(data.get("auto", True)))

        entries = data.get("entries") or []
        if entries:
            self._set_text(entries)

        resolved = data.get("resolved") or {}
        nres = sum(len(v) for v in resolved.values())
        parts = [
            "режим: {}".format(MODE_LABELS[MODES.index(mode)] if mode in MODES else mode),
            "правил установлено: {}".format(len(data.get("rules_installed", []))),
            "шлюз: {} ({})".format(data.get("gateway") or "—",
                                   data.get("wan_device") or "—"),
        ]
        if resolved:
            parts.append("доменов разрешено: {} → {} адресов".format(
                len(resolved), nres))
        self.state_row.set_subtitle(" · ".join(parts))
        self._update_counter()
        return False

    # ------------------------------------------------------------------ #
    def _busy(self, on):
        self.apply_btn.set_sensitive(not on)
        self.spinner.set_visible(on)
        self.spinner.start() if on else self.spinner.stop()

    def _on_apply(self, _btn):
        entries, bad = presets.parse_entries(self._get_text())
        if bad:
            self.toasts.add_toast(
                Adw.Toast.new("Некорректные записи: " + ", ".join(bad[:3])))
            return

        mode = MODES[self.mode_row.get_selected()]
        if mode != "off" and not entries:
            self.toasts.add_toast(Adw.Toast.new("Список пуст"))
            return

        iface = None
        if mode != "off":
            up = (getattr(self.app, "_last", None) or {}).get("up") or []
            if not up:
                self.toasts.add_toast(Adw.Toast.new("Сначала подключите туннель"))
                return
            iface = up[0]

        self._busy(True)
        auto = self.auto_row.get_active()

        def job():
            err, data = None, None
            try:
                data = backend.split_apply(iface, mode, entries, auto)
            except backend.HelperError as exc:
                err = str(exc)
            GLib.idle_add(done, err, data)

        def done(err, data):
            self._busy(False)
            if err:
                self.toasts.add_toast(Adw.Toast.new("Ошибка: " + err))
            else:
                cnt = (data or {}).get("count", 0)
                self.toasts.add_toast(
                    Adw.Toast.new("Применено, адресов в правилах: {}".format(cnt)))
            self._load()
            return False

        threading.Thread(target=job, daemon=True).start()
