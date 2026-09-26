# -*- coding: utf-8 -*-
"""Окно настроек AWG Manager (Adw.PreferencesWindow)."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .settings import STAT_ROWS, settings
from .compat import switch_row as _switch_row


class PrefsWindow(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=False)
        self.set_title("Настройки")
        self.set_default_size(560, 700)
        self.set_search_enabled(True)

        self._build_display()
        self._build_stats()
        self._build_behaviour()

    # ------------------------------------------------------------------ #
    def _switch(self, group, key, title, subtitle=""):
        row = _switch_row(title, subtitle)
        row.set_active(bool(settings.get(key)))
        row.connect("notify::active", lambda r, _p: settings.set(key, r.get_active()))
        group.add(row)
        return row

    # ------------------------------------------------------------------ #
    def _build_display(self):
        page = Adw.PreferencesPage(title="Интерфейс", icon_name="applications-graphics-symbolic")

        g = Adw.PreferencesGroup(
            title="Элементы главного окна",
            description="Что показывать в окне приложения",
        )
        self._switch(g, "show_status_header", "Панель состояния",
                     "Крупный значок VPN, статус и время подключения")
        self._switch(g, "show_graph", "График скорости",
                     "Спарклайн приёма и передачи в реальном времени")
        self._switch(g, "show_autostart", "Блок автозапуска",
                     "Переключатель запуска туннеля при загрузке системы")
        self._switch(g, "show_conf_subtitle", "Подписи у туннелей",
                     "Состояние и handshake под именем каждого конфига")
        page.add(g)

        gt = Adw.PreferencesGroup(
            title="Значок в трее",
            description="Изменения применяются после перезапуска awg-manager-tray",
        )
        self._switch(gt, "tray_stats", "Трафик в меню трея",
                     "Строка со счётчиками и временем handshake")
        self._switch(gt, "tray_label", "Текст рядом со значком",
                     "Имя активного туннеля в панели (поддерживается не всеми оболочками)")
        page.add(gt)

        self.add(page)

    def _build_stats(self):
        page = Adw.PreferencesPage(title="Статистика", icon_name="utilities-system-monitor-symbolic")
        g = Adw.PreferencesGroup(
            title="Отображаемые данные",
            description="Строки в блоке «Статистика соединения»",
        )
        for key, title, subtitle in STAT_ROWS:
            self._switch(g, key, title, subtitle)
        page.add(g)

        ga = Adw.PreferencesGroup(title="Сброс")
        row = Adw.ActionRow(
            title="Восстановить настройки по умолчанию",
            subtitle="Вернуть все параметры к исходным значениям",
        )
        btn = Gtk.Button(label="Сбросить", valign=Gtk.Align.CENTER)
        btn.add_css_class("destructive-action")
        btn.connect("clicked", self._on_reset)
        row.add_suffix(btn)
        ga.add(row)
        page.add(ga)

        self.add(page)

    def _build_behaviour(self):
        page = Adw.PreferencesPage(title="Поведение", icon_name="preferences-system-symbolic")

        g = Adw.PreferencesGroup(title="Туннели")
        self._switch(
            g, "exclusive", "Только один туннель одновременно",
            "При включении нового туннеля предыдущий будет отключён",
        )
        self._switch(g, "notifications", "Системные уведомления",
                     "Сообщать о подключении, отключении и ошибках")
        page.add(g)

        g2 = Adw.PreferencesGroup(
            title="Обновление данных",
            description="Слишком частый опрос слегка нагружает систему",
        )
        if hasattr(Adw, "SpinRow"):
            row = Adw.SpinRow.new_with_range(0.5, 10.0, 0.5)
            row.set_title("Период опроса")
            row.set_subtitle("Секунд между обновлениями статистики")
            row.set_digits(1)
            row.set_value(float(settings.get("interval", 1.0)))
            row.connect("notify::value", self._on_interval)
            g2.add(row)

        if hasattr(Adw, "SpinRow"):
            row2 = Adw.SpinRow.new_with_range(30, 600, 10)
            row2.set_title("Точек на графике")
            row2.set_subtitle("Глубина истории скорости")
            row2.set_value(int(settings.get("graph_points", 120)))
            row2.connect(
                "notify::value",
                lambda r, _p: settings.set("graph_points", int(r.get_value())),
            )
            g2.add(row2)
        page.add(g2)

        self.add(page)

    # ------------------------------------------------------------------ #
    def _on_interval(self, row, _p):
        settings.set("interval", round(float(row.get_value()), 1))

    def _on_reset(self, _btn):
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading="Сбросить настройки?",
            body="Все параметры отображения вернутся к значениям по умолчанию.",
        )
        dlg.add_response("cancel", "Отмена")
        dlg.add_response("reset", "Сбросить")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._reset_done)
        dlg.present()

    def _reset_done(self, dlg, response):
        if response == "reset":
            settings.reset()
            self.close()
