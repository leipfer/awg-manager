# -*- coding: utf-8 -*-
"""
Настройки AWG Manager.

Хранятся в ~/.config/awg-manager/settings.json.
Читаются и главным окном, и значком в трее.
"""

import json
import os
import threading

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "awg-manager",
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS = {
    # какие строки статистики показывать
    "show_handshake": True,
    "show_rx": True,
    "show_tx": True,
    "show_speed": True,
    "show_endpoint": True,
    "show_address": True,
    "show_dns": True,
    "show_mtu": False,
    "show_peer_key": False,
    "show_allowed_ips": False,
    "show_keepalive": False,
    # блоки интерфейса
    "show_graph": True,
    "show_status_header": True,
    "show_autostart": True,
    "show_conf_subtitle": True,
    # поведение
    "exclusive": True,          # только один туннель одновременно
    "notifications": True,
    "interval": 1.0,            # период опроса, секунды
    "graph_points": 120,
    # трей
    "tray_stats": True,
    "tray_label": False,        # текст рядом со значком в панели
}

# Описание строк статистики: ключ -> (заголовок, подпись в настройках)
STAT_ROWS = [
    ("show_handshake", "Последний handshake", "Время последнего рукопожатия с сервером"),
    ("show_rx", "Принято", "Объём полученных данных"),
    ("show_tx", "Передано", "Объём отправленных данных"),
    ("show_speed", "Скорость", "Текущая скорость приёма и передачи"),
    ("show_endpoint", "Endpoint", "Адрес и порт сервера"),
    ("show_address", "Адрес интерфейса", "IP-адрес внутри туннеля"),
    ("show_dns", "DNS", "DNS-серверы из конфигурации"),
    ("show_mtu", "MTU", "Размер MTU интерфейса"),
    ("show_peer_key", "Публичный ключ пира", "Публичный ключ сервера"),
    ("show_allowed_ips", "AllowedIPs", "Маршрутизируемые через туннель подсети"),
    ("show_keepalive", "Keepalive", "Интервал keepalive-пакетов"),
]

_lock = threading.Lock()


class Settings:
    def __init__(self):
        self._data = dict(DEFAULTS)
        self._watchers = []
        self.load()

    # ------------------------------------------------------------------ #
    def load(self):
        try:
            with open(CONFIG_FILE, "r") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
                        self._data[k] = v
                    elif k in DEFAULTS and isinstance(DEFAULTS[k], float):
                        try:
                            self._data[k] = float(v)
                        except (TypeError, ValueError):
                            pass
        except (OSError, ValueError):
            pass
        return self

    def save(self):
        with _lock:
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                tmp = CONFIG_FILE + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(self._data, fh, indent=2, ensure_ascii=False)
                os.replace(tmp, CONFIG_FILE)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    def get(self, key, default=None):
        return self._data.get(key, DEFAULTS.get(key, default))

    def __getitem__(self, key):
        return self.get(key)

    def set(self, key, value):
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self.save()
        for cb in list(self._watchers):
            try:
                cb(key, value)
            except Exception:
                pass

    def reset(self):
        self._data = dict(DEFAULTS)
        self.save()
        for cb in list(self._watchers):
            try:
                cb(None, None)
            except Exception:
                pass

    def connect(self, callback):
        """callback(key, value); key=None означает полный сброс."""
        self._watchers.append(callback)

    def as_dict(self):
        return dict(self._data)


settings = Settings()
