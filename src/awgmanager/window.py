# -*- coding: utf-8 -*-
"""Главное окно AWG Manager (GTK4 + libadwaita)."""

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk, Pango  # noqa: E402

from . import backend  # noqa: E402
from .settings import STAT_ROWS, settings  # noqa: E402
from .compat import switch_row as _switch_row  # noqa: E402

ACCENT_UP = "#60F75C"

CSS = """
.awg-header {
  padding: 22px 16px 18px 16px;
  border-bottom: 1px solid alpha(currentColor, 0.10);
}
.awg-icon-up    { color: %(up)s; }
.awg-icon-wait  { color: #f5a623; }
.awg-icon-down  { opacity: 0.45; }
.awg-title      { font-size: 19pt; font-weight: 800; }
.awg-title-up   { color: %(up)s; }
.awg-subtitle   { font-size: 11pt; opacity: 0.7; }
.awg-dot-up     { color: %(up)s; }
""" % {"up": ACCENT_UP}


def _install_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode())
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


class SpeedGraph(Gtk.DrawingArea):
    """Компактный спарклайн скорости приёма/передачи."""

    def __init__(self):
        super().__init__()
        self.rx = []
        self.tx = []
        self.set_content_height(96)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

    @property
    def max_points(self):
        return max(30, int(settings.get("graph_points", 120)))

    def push(self, rx_bps, tx_bps):
        self.rx.append(max(0.0, rx_bps))
        self.tx.append(max(0.0, tx_bps))
        n = self.max_points
        del self.rx[: max(0, len(self.rx) - n)]
        del self.tx[: max(0, len(self.tx) - n)]
        self.queue_draw()

    def clear(self):
        self.rx.clear()
        self.tx.clear()
        self.queue_draw()

    def _draw(self, area, cr, width, height, *_):
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.10)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        peak = max([1024.0] + self.rx + self.tx)
        pad = 5
        h = height - pad * 2
        n = self.max_points

        for series, color in (
            (self.rx, (0.376, 0.969, 0.361)),   # #60F75C — приём
            (self.tx, (0.25, 0.55, 0.95)),      # передача
        ):
            if len(series) < 2:
                continue
            step = width / float(n - 1)
            offset = width - step * (len(series) - 1)

            cr.set_source_rgba(*color, 0.18)
            cr.move_to(offset, height - pad)
            for i, v in enumerate(series):
                cr.line_to(offset + step * i, pad + h - (v / peak) * h)
            cr.line_to(offset + step * (len(series) - 1), height - pad)
            cr.close_path()
            cr.fill()

            cr.set_source_rgba(*color, 0.95)
            cr.set_line_width(1.8)
            for i, v in enumerate(series):
                x = offset + step * i
                y = pad + h - (v / peak) * h
                cr.line_to(x, y) if i else cr.move_to(x, y)
            cr.stroke()

        cr.set_source_rgba(0.5, 0.5, 0.5, 0.85)
        cr.select_font_face("Sans")
        cr.set_font_size(10)
        cr.move_to(7, 15)
        cr.show_text("пик ~ " + backend.human_speed(peak))


class StatusHeader(Gtk.Box):
    """
    Фиксированная панель состояния.

    Живёт ВНЕ прокручиваемой области, поэтому не сжимается по вертикали,
    как это делал Adw.StatusPage внутри ScrolledWindow.
    """

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("awg-header")
        self.set_halign(Gtk.Align.FILL)

        self.icon = Gtk.Image.new_from_icon_name("network-vpn-disabled-symbolic")
        self.icon.set_pixel_size(64)
        self.icon.add_css_class("awg-icon-down")
        self.icon.set_halign(Gtk.Align.CENTER)
        self.append(self.icon)

        self.title = Gtk.Label(label="Отключено")
        self.title.add_css_class("awg-title")
        self.title.set_halign(Gtk.Align.CENTER)
        self.append(self.title)

        self.subtitle = Gtk.Label(label="Ни один туннель не активен")
        self.subtitle.add_css_class("awg-subtitle")
        self.subtitle.set_halign(Gtk.Align.CENTER)
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.append(self.subtitle)

    def set_state(self, state, title, subtitle):
        """state: 'up' | 'wait' | 'down'."""
        for cls in ("awg-icon-up", "awg-icon-wait", "awg-icon-down"):
            self.icon.remove_css_class(cls)
        self.title.remove_css_class("awg-title-up")

        if state == "up":
            self.icon.set_from_icon_name("network-vpn-symbolic")
            self.icon.add_css_class("awg-icon-up")
            self.title.add_css_class("awg-title-up")
        elif state == "wait":
            self.icon.set_from_icon_name("network-vpn-acquiring-symbolic")
            self.icon.add_css_class("awg-icon-wait")
        else:
            self.icon.set_from_icon_name("network-vpn-disabled-symbolic")
            self.icon.add_css_class("awg-icon-down")

        self.title.set_text(title)
        self.subtitle.set_text(subtitle)


class ConfigRow(Adw.ActionRow):
    """Строка конфига с переключателем."""

    def __init__(self, name, on_toggle):
        super().__init__()
        self.name = name
        self._on_toggle = on_toggle
        self._guard = False

        self.set_title(name)
        self.set_subtitle("остановлен")

        self.dot = Gtk.Image.new_from_icon_name("media-record-symbolic")
        self.dot.add_css_class("dim-label")
        self.add_prefix(self.dot)

        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        self.add_suffix(self.spinner)

        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", self._toggled)
        self.add_suffix(self.switch)
        self.set_activatable_widget(self.switch)

    def _toggled(self, sw, _p):
        if self._guard:
            return
        self._on_toggle(self.name, sw.get_active())

    def set_state(self, up, subtitle):
        self._guard = True
        self.switch.set_active(up)
        self._guard = False
        if settings.get("show_conf_subtitle"):
            self.set_subtitle(subtitle)
        else:
            self.set_subtitle("")
        if up:
            self.dot.remove_css_class("dim-label")
            self.dot.add_css_class("awg-dot-up")
        else:
            self.dot.remove_css_class("awg-dot-up")
            self.dot.add_css_class("dim-label")

    def set_busy(self, busy):
        self.switch.set_sensitive(not busy)
        self.spinner.set_visible(busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()


class AwgWindow(Adw.ApplicationWindow):
    def __init__(self, app, **kw):
        super().__init__(application=app, **kw)
        _install_css()

        self.app = app
        self.rows = {}
        self.stat_rows = {}
        self._prev = {}
        self._busy = set()
        self._selected = None

        self.set_title("AmneziaWG Manager")
        self.set_default_size(660, 780)
        self.set_size_request(420, 480)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_btn.set_tooltip_text("Обновить")
        self.refresh_btn.connect("clicked", lambda *_: self.app.force_refresh())
        header.pack_start(self.refresh_btn)

        menu = Gio.Menu()
        menu.append("Настройки", "app.prefs")
        menu.append("Раздельное туннелирование", "app.split")
        menu.append("Отключить все туннели", "app.down-all")
        menu.append("Показать журнал", "app.logs")
        menu.append("О программе", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        toolbar.add_top_bar(header)

        self.toasts = Adw.ToastOverlay()
        toolbar.set_content(self.toasts)
        self.set_content(toolbar)

        # корневой контейнер: шапка фиксирована, ниже — прокрутка
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toasts.set_child(root)

        self.banner = Adw.Banner(revealed=False)
        root.append(self.banner)

        self.status_header = StatusHeader()
        root.append(self.status_header)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root.append(scroller)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        scroller.set_child(box)

        # --- туннели ---
        self.cfg_group = Adw.PreferencesGroup(title="Туннели")
        self.cfg_group.set_description("Конфигурации из /etc/amnezia/amneziawg")
        box.append(self.cfg_group)

        # --- статистика ---
        self.stats_group = Adw.PreferencesGroup(title="Статистика соединения")
        self.stats_group.set_visible(False)
        box.append(self.stats_group)

        for key, title, _sub in STAT_ROWS:
            row = Adw.ActionRow(title=title)
            lbl = Gtk.Label(label="—", selectable=True)
            lbl.add_css_class("dim-label")
            lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lbl.set_max_width_chars(34)
            row.add_suffix(lbl)
            row._value = lbl
            self.stats_group.add(row)
            self.stat_rows[key] = row

        # --- график ---
        self.graph_group = Adw.PreferencesGroup(title="Скорость в реальном времени")
        self.graph_group.set_description("зелёный — приём, синий — передача")
        self.graph_group.set_visible(False)
        self.graph = SpeedGraph()
        frame = Gtk.Frame()
        frame.set_child(self.graph)
        self.graph_group.add(frame)
        box.append(self.graph_group)

        # --- автозапуск ---
        self.auto_group = Adw.PreferencesGroup(title="Дополнительно")
        self.auto_row = _switch_row(
            "Автозапуск при загрузке системы",
            "Выберите активный туннель",
        )
        self.auto_row.set_sensitive(False)
        self._auto_guard = False
        self.auto_row.connect("notify::active", self._on_auto)
        self.auto_group.add(self.auto_row)
        box.append(self.auto_group)

        settings.connect(self._on_setting_changed)
        self.apply_settings()

    # ------------------------------------------------------------------ #
    def apply_settings(self):
        self.status_header.set_visible(bool(settings.get("show_status_header")))
        self.auto_group.set_visible(bool(settings.get("show_autostart")))
        for key, row in self.stat_rows.items():
            row.set_visible(bool(settings.get(key)))
        if not settings.get("show_graph"):
            self.graph_group.set_visible(False)

    def _on_setting_changed(self, key, _value):
        self.apply_settings()
        if key == "interval":
            self.app.restart_monitor()

    # ------------------------------------------------------------------ #
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast.new(text))

    def set_error(self, text):
        if text:
            self.banner.set_title(text)
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def _on_toggle(self, name, active):
        self.app.toggle(name, active)

    def _on_auto(self, row, _p):
        if self._auto_guard or not self._selected:
            return
        self.app.set_autostart(self._selected, row.get_active())

    def mark_busy(self, name, busy):
        if busy:
            self._busy.add(name)
        else:
            self._busy.discard(name)
        row = self.rows.get(name)
        if row:
            row.set_busy(busy)

    def sync_switch(self, name, active):
        """Принудительно вернуть переключатель в нужное положение."""
        row = self.rows.get(name)
        if row:
            row.set_state(active, row.get_subtitle() or "")

    # ------------------------------------------------------------------ #
    def update(self, st):
        self.set_error(None)
        configs = st.get("configs", [])
        names = [c["name"] for c in configs]

        if set(names) != set(self.rows):
            for row in self.rows.values():
                self.cfg_group.remove(row)
            self.rows.clear()
            for name in names:
                row = ConfigRow(name, self._on_toggle)
                self.cfg_group.add(row)
                self.rows[name] = row
            if not names:
                self.set_error("Конфигурации не найдены в /etc/amnezia/amneziawg")

        up_list = st.get("up", [])
        ifaces = st.get("interfaces", {})
        now = st.get("ts", time.time())

        for c in configs:
            row = self.rows.get(c["name"])
            if not row:
                continue
            det = ifaces.get(c["name"], {})
            peers = det.get("peers", [])
            if c["up"] and peers:
                hs = max((p.get("latest_handshake", 0) for p in peers), default=0)
                sub = "подключён · handshake " + backend.human_ago(hs, now)
            elif c["up"]:
                sub = "интерфейс поднят"
            else:
                sub = "остановлен"
            if c["enabled"] == "enabled":
                sub += " · автозапуск"
            row.set_state(c["up"], sub)
            row.set_busy(c["name"] in self._busy)

        active = up_list[0] if up_list else None
        self._selected = active

        if len(up_list) > 1:
            self.set_error(
                "Активно несколько туннелей: {}. Включите «Только один туннель» "
                "в настройках или отключите лишние.".format(", ".join(up_list))
            )

        if active:
            self._update_active(active, ifaces.get(active, {}), configs, now)
        else:
            self._update_idle()

    def _update_active(self, active, det, configs, now):
        peers = det.get("peers", [])
        rx = sum(p.get("rx", 0) for p in peers)
        tx = sum(p.get("tx", 0) for p in peers)
        hs = max((p.get("latest_handshake", 0) for p in peers), default=0)
        fresh = bool(hs) and (now - hs) < 190

        self.status_header.set_state(
            "up" if fresh else "wait",
            "Подключено" if fresh else "Устанавливается…",
            "{} · handshake {}".format(active, backend.human_ago(hs, now)),
        )

        prev = self._prev.get(active)
        if prev:
            dt = max(0.2, now - prev[0])
            rxs = max(0, rx - prev[1]) / dt
            txs = max(0, tx - prev[2]) / dt
        else:
            rxs = txs = 0.0
        self._prev[active] = (now, rx, tx)
        self.graph.push(rxs, txs)

        peer = peers[0] if peers else {}
        conf = det.get("conf", {})
        vals = {
            "show_handshake": backend.human_ago(hs, now),
            "show_rx": backend.human_bytes(rx),
            "show_tx": backend.human_bytes(tx),
            "show_speed": "↓ {}   ↑ {}".format(
                backend.human_speed(rxs), backend.human_speed(txs)
            ),
            "show_endpoint": peer.get("endpoint") or conf.get("endpoint") or "—",
            "show_address": ", ".join(det.get("addresses") or [])
            or conf.get("address")
            or "—",
            "show_dns": conf.get("dns") or "—",
            "show_mtu": str(det.get("mtu") or conf.get("mtu") or "—"),
            "show_peer_key": peer.get("public_key") or "—",
            "show_allowed_ips": ", ".join(peer.get("allowed_ips") or []) or "—",
            "show_keepalive": (peer.get("keepalive") + " с")
            if peer.get("keepalive")
            else "выкл",
        }
        for key, text in vals.items():
            row = self.stat_rows.get(key)
            if row:
                row._value.set_text(text)

        self.stats_group.set_visible(
            any(settings.get(k) for k, _t, _s in STAT_ROWS)
        )
        self.graph_group.set_visible(bool(settings.get("show_graph")))

        enabled = next(
            (c["enabled"] == "enabled" for c in configs if c["name"] == active), False
        )
        self._auto_guard = True
        self.auto_row.set_sensitive(True)
        self.auto_row.set_active(enabled)
        self.auto_row.set_subtitle("Туннель {}".format(active))
        self._auto_guard = False

    def _update_idle(self):
        self.status_header.set_state(
            "down", "Отключено", "Ни один туннель не активен"
        )
        self.stats_group.set_visible(False)
        self.graph_group.set_visible(False)
        self.graph.clear()
        self._prev.clear()
        self._auto_guard = True
        self.auto_row.set_sensitive(False)
        self.auto_row.set_active(False)
        self.auto_row.set_subtitle("Выберите активный туннель")
        self._auto_guard = False

    # ------------------------------------------------------------------ #
    def show_logs(self, text):
        dlg = Adw.Window(transient_for=self, modal=True, title="Журнал")
        dlg.set_default_size(780, 540)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        view = Gtk.TextView(editable=False, monospace=True)
        view.get_buffer().set_text(text or "Записей нет.")
        for side in ("top", "bottom", "start", "end"):
            getattr(view, "set_margin_" + side)(8)
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.set_child(view)
        tv.set_content(sc)
        dlg.set_content(tv)
        dlg.present()
