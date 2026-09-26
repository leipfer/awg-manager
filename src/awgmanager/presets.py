# -*- coding: utf-8 -*-
"""
Наборы подсетей: встроенные и пользовательские.

Пользовательские наборы хранятся в ~/.config/awg-manager/sets.json
и поддерживают импорт/экспорт обычным текстовым файлом.
"""

import ipaddress
import json
import os
import re

from .settings import CONFIG_DIR

SETS_FILE = os.path.join(CONFIG_DIR, "sets.json")

# --------------------------------------------------------------------------- #
# Встроенные наборы
# --------------------------------------------------------------------------- #
# Российские сервисы собраны в ОДИН набор: Яндекс, VK/Mail.ru, Сбербанк,
# Госуслуги, Тинькофф, Альфа-банк, МТС, Ростелеком, Avito, Ozon, WB, Rutube.
# Списки приблизительные: у крупных сервисов адреса меняются, поэтому
# надёжнее добавлять сами домены — приложение их разрешит.
RUSSIAN_IPS = [
    # Яндекс
    "5.45.192.0/18", "5.255.192.0/18", "37.9.64.0/18", "37.140.128.0/18",
    "77.88.0.0/18", "84.201.128.0/18", "87.250.224.0/19", "93.158.128.0/18",
    "95.108.128.0/17", "100.43.64.0/19", "141.8.128.0/18", "178.154.128.0/17",
    "213.180.192.0/19",
    # VK / Mail.ru
    "87.240.128.0/18", "93.186.224.0/20", "95.142.192.0/20", "128.140.168.0/21",
    "185.32.187.0/24", "185.32.184.0/22", "217.20.144.0/20", "217.69.128.0/20",
    # Сбербанк
    "194.54.14.0/24", "185.157.96.0/22", "37.9.32.0/20",
    # Госуслуги / инфраструктура
    "212.193.128.0/19", "159.255.192.0/19", "178.248.232.0/21",
    # Тинькофф
    "91.194.226.0/23", "213.85.128.0/19",
    # Альфа-банк
    "89.208.208.0/21", "185.68.16.0/22",
    # МТС
    "213.87.0.0/16", "94.25.0.0/16",
    # Ростелеком
    "213.59.0.0/16", "80.75.128.0/18",
    # Avito
    "185.68.152.0/22", "5.61.232.0/21",
    # Ozon
    "185.71.76.0/22", "212.193.160.0/20",
    # Wildberries
    "178.176.128.0/17",
    # Rutube
    "185.180.16.0/22",
]

# Домены российских сервисов — резолвятся при применении, поэтому
# всегда актуальны в отличие от захардкоженных подсетей.
RUSSIAN_DOMAINS = [
    "gosuslugi.ru", "sberbank.ru", "online.sberbank.ru", "tinkoff.ru",
    "alfabank.ru", "vtb.ru", "nalog.gov.ru", "mos.ru", "pfr.gov.ru",
    "yandex.ru", "ya.ru", "mail.ru", "vk.com", "ok.ru", "avito.ru",
    "ozon.ru", "wildberries.ru", "dns-shop.ru", "mvideo.ru",
    "rutube.ru", "kinopoisk.ru", "2gis.ru", "hh.ru", "drom.ru",
    "gismeteo.ru", "rzd.ru", "aeroflot.ru", "pochta.ru",
]

BUILTIN = {
    "Российские сервисы (IP)": {
        "entries": RUSSIAN_IPS,
        "description": "Объединённый список: банки, госуслуги, Яндекс, VK, "
                       "маркетплейсы, операторы связи",
    },
    "Российские сервисы (домены)": {
        "entries": RUSSIAN_DOMAINS,
        "description": "Те же сервисы доменами — адреса определяются "
                       "автоматически и всегда актуальны",
    },
    "Локальные сети (RFC1918)": {
        "entries": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                    "169.254.0.0/16"],
        "description": "Домашняя сеть, принтеры, NAS — почти всегда нужны напрямую",
    },
}


# --------------------------------------------------------------------------- #
# Проверка записей
# --------------------------------------------------------------------------- #
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


def clean_domain(raw):
    s = (raw or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0]
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    return s.split(":")[0].strip(".")


def classify_entry(raw):
    """Вернуть ('net'|'domain'|'bad', нормализованное значение)."""
    raw = (raw or "").strip()
    if not raw or raw.startswith("#"):
        return "skip", ""
    try:
        net = ipaddress.ip_network(raw, strict=False)
        if net.version != 4:
            return "bad", raw
        return "net", str(net)
    except ValueError:
        pass
    dom = clean_domain(raw)
    if DOMAIN_RE.match(dom):
        return "domain", dom
    return "bad", raw


def parse_entries(text):
    """Разобрать многострочный текст. Возвращает (записи, некорректные)."""
    good, bad = [], []
    for line in (text or "").replace(",", "\n").splitlines():
        kind, value = classify_entry(line)
        if kind == "skip":
            continue
        if kind == "bad":
            bad.append(value)
        elif value not in good:
            good.append(value)
    return good, bad


# --------------------------------------------------------------------------- #
# Пользовательские наборы
# --------------------------------------------------------------------------- #
def load_sets():
    try:
        with open(SETS_FILE) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {
                k: {"entries": [str(e) for e in v.get("entries", [])],
                    "description": str(v.get("description", ""))}
                for k, v in data.items()
                if isinstance(v, dict)
            }
    except (OSError, ValueError):
        pass
    return {}


def save_sets(sets):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = SETS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(sets, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, SETS_FILE)


MAX_NAME = 60


def save_set(name, entries, description="", overwrite=False):
    """Сохранить пользовательский набор.

    overwrite=False защищает от случайной замены существующего набора:
    вызывающий код обязан подтвердить намерение явно.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("укажите название набора")
    if len(name) > MAX_NAME:
        raise ValueError("слишком длинное название (максимум {} символов)"
                         .format(MAX_NAME))
    if name in BUILTIN:
        raise ValueError("нельзя перезаписать встроенный набор — "
                         "выберите другое название")
    if not entries:
        raise ValueError("набор пуст")

    sets = load_sets()
    if name in sets and not overwrite:
        raise ValueError("набор «{}» уже существует — "
                         "включите перезапись или смените название".format(name))

    sets[name] = {"entries": list(entries), "description": description or ""}
    save_sets(sets)
    return sets


def delete_set(name):
    sets = load_sets()
    if name in sets:
        del sets[name]
        save_sets(sets)
    return sets


def all_sets():
    """Встроенные + пользовательские, с пометкой builtin."""
    out = []
    for name, data in BUILTIN.items():
        out.append({"name": name, "builtin": True, **data})
    for name, data in sorted(load_sets().items()):
        out.append({"name": name, "builtin": False,
                    "entries": data["entries"],
                    "description": data.get("description", "")})
    return out


def export_text(entries, name=""):
    head = "# Набор AWG Manager"
    if name:
        head += ": " + name
    return head + "\n" + "\n".join(entries) + "\n"


def import_text(text):
    """Разобрать файл набора. Возвращает (записи, некорректные)."""
    return parse_entries(text)
