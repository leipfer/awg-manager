# -*- coding: utf-8 -*-
"""
Клиентская обёртка над привилегированным хелпером awg-helper.

Все root-операции выполняются через pkexec; правило polkit
(org.awgmanager.manage) разрешает их активной локальной сессии
без запроса пароля.
"""

import json
import os
import shutil
import subprocess
import threading

HELPER = os.environ.get("AWG_HELPER", "/usr/libexec/awg-manager/awg-helper")
PKEXEC = shutil.which("pkexec") or "/usr/bin/pkexec"


class HelperError(Exception):
    pass


def helper_path():
    if os.path.exists(HELPER):
        return HELPER
    local = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "helper",
        "awg-helper",
    )
    return local if os.path.exists(local) else HELPER


def _cmd(args):
    if os.geteuid() == 0:
        return [helper_path()] + list(args)
    return [PKEXEC, helper_path()] + list(args)


def call(*args, timeout=90):
    """Синхронный вызов хелпера, возвращает разобранный JSON."""
    try:
        p = subprocess.run(_cmd(args), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise HelperError("pkexec или awg-helper не найден. Переустановите пакет.")
    except subprocess.TimeoutExpired:
        raise HelperError("Превышено время ожидания ответа от awg-helper.")

    out = (p.stdout or "").strip()
    if not out:
        err = (p.stderr or "").strip()
        if p.returncode == 126 or "dismissed" in err.lower() or "not authorized" in err.lower():
            raise HelperError("Запрос прав отклонён (polkit).")
        raise HelperError(err or "awg-helper не вернул данных (код {}).".format(p.returncode))

    try:
        data = json.loads(out.splitlines()[-1])
    except ValueError:
        raise HelperError("Некорректный ответ awg-helper: {}".format(out[:300]))

    if not data.get("ok", True):
        raise HelperError(data.get("error") or data.get("stderr") or "Неизвестная ошибка.")
    return data


# --------------------------------------------------------------------------- #
# высокоуровневые операции
# --------------------------------------------------------------------------- #
def list_configs():
    return call("list").get("configs", [])


def status(iface=None):
    return call("status", iface) if iface else call("status")


def up(iface):
    return call("up", iface, timeout=120)


def switch(iface):
    """Поднять iface, предварительно опустив все остальные туннели."""
    return call("switch", iface, timeout=180)


def down(iface):
    return call("down", iface, timeout=120)


def restart(iface):
    return call("restart", iface, timeout=120)


def down_all():
    return call("down-all", timeout=180)


def set_autostart(iface, enabled):
    return call("enable" if enabled else "disable", iface)


def split_show():
    return call("split-show", timeout=15)


def split_apply(iface, mode, entries, auto=True):
    return call("split-apply", iface or "", mode, ",".join(entries),
                "1" if auto else "0", timeout=180)


def split_verify(target):
    return call("split-verify", target, timeout=60)


def split_resolve(domains):
    return call("split-resolve", ",".join(domains), timeout=120)


def logs(iface, lines=80):
    return call("logs", iface, str(lines)).get("logs", "")


class Monitor(threading.Thread):
    """
    Держит один долгоживущий привилегированный процесс `awg-helper monitor`
    и вызывает on_status(dict) для каждой полученной строки.
    Так пароль/авторизация запрашивается один раз, а не каждую секунду.
    """

    def __init__(self, on_status, on_error=None, interval=1.0):
        super().__init__(daemon=True)
        self.on_status = on_status
        self.on_error = on_error
        self.interval = interval
        self._proc = None
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    _cmd(["monitor", str(self.interval)]),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                if self.on_error:
                    self.on_error(str(exc))
                return

            for line in self._proc.stdout:
                if self._stop.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    self.on_status(json.loads(line))
                except ValueError:
                    continue

            if self._stop.is_set():
                return
            err = ""
            if self._proc.stderr:
                err = (self._proc.stderr.read() or "").strip()
            if self.on_error:
                self.on_error(err or "Поток мониторинга прерван, переподключение…")
            if self._stop.wait(3):
                return

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# форматирование
# --------------------------------------------------------------------------- #
def human_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ"):
        if abs(n) < 1024.0:
            return "{:.1f} {}".format(n, unit) if unit != "Б" else "{:.0f} Б".format(n)
        n /= 1024.0
    return "{:.1f} ПиБ".format(n)


def human_ago(ts, now=None):
    import time as _t

    if not ts:
        return "никогда"
    now = now or _t.time()
    d = int(max(0, now - ts))
    if d < 5:
        return "только что"
    if d < 60:
        return "{} с назад".format(d)
    if d < 3600:
        return "{} мин {} с назад".format(d // 60, d % 60)
    if d < 86400:
        return "{} ч {} мин назад".format(d // 3600, (d % 3600) // 60)
    return "{} дн назад".format(d // 86400)


def human_speed(bps):
    return human_bytes(bps) + "/с"
