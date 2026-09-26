# -*- coding: utf-8 -*-
"""Тесты привилегированного хелпера и модуля раздельного туннелирования."""

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(name, loader)
    )
    loader.exec_module(mod)
    return mod


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.h = load("awg_helper", "helper/awg-helper")
        self.dir = tempfile.mkdtemp()
        self.h.CONF_DIR = self.dir
        self.h.AWG = "/usr/bin/awg"
        for name in ("awg0", "xbxWARPv1_20"):
            with open(os.path.join(self.dir, name + ".conf"), "w") as fh:
                fh.write(
                    "[Interface]\nPrivateKey = SECRETKEY=\n"
                    "Address = 10.8.0.2/32\nDNS = 1.1.1.1\nMTU = 1420\n"
                    "[Peer]\nPublicKey = PUB=\nEndpoint = 1.2.3.4:51820\n"
                )

    # ------------------------------------------------------------------ #
    def test_list_configs(self):
        self.assertEqual(self.h.list_configs(), ["awg0", "xbxWARPv1_20"])

    def test_valid_iface_rejects_traversal(self):
        self.assertTrue(self.h.valid_iface("awg0"))
        for bad in ("../../etc/shadow", "nope", "", ".", "..", "a" * 40, "awg0;rm -rf /"):
            self.assertFalse(self.h.valid_iface(bad), bad)

    def test_conf_meta_hides_private_key(self):
        meta = self.h.conf_meta("awg0")
        self.assertEqual(meta["dns"], "1.1.1.1")
        self.assertEqual(meta["address"], "10.8.0.2/32")
        self.assertNotIn("privatekey", meta)
        self.assertNotIn("SECRETKEY", json.dumps(meta))

    def test_parse_dump_never_leaks_private_key(self):
        now = int(time.time())
        dump = (
            "PRIVKEYLEAK=\tSRVPUB=\t51820\toff\n"
            "PEERPUB=\t(none)\t1.2.3.4:51820\t0.0.0.0/0\t"
            "{}\t123456789\t9876543\t25".format(now - 30)
        )
        self.h.run = lambda cmd, timeout=25: (0, dump, "")
        info = self.h.parse_dump("awg0")
        self.assertNotIn("PRIVKEYLEAK", json.dumps(info))
        peer = info["peers"][0]
        self.assertEqual(peer["rx"], 123456789)
        self.assertEqual(peer["tx"], 9876543)
        self.assertEqual(peer["keepalive"], "25")
        self.assertEqual(peer["allowed_ips"], ["0.0.0.0/0"])

    def test_parse_dump_handles_garbage(self):
        self.h.run = lambda cmd, timeout=25: (0, "мусор\nне\tтабы", "")
        self.assertEqual(self.h.parse_dump("awg0")["peers"], [])


class SwitchTest(unittest.TestCase):
    """Эксклюзивное переключение: одновременно активен только один туннель."""

    def setUp(self):
        self.h = load("awg_helper2", "helper/awg-helper")
        self.dir = tempfile.mkdtemp()
        self.h.CONF_DIR = self.dir
        self.h.AWG = "/usr/bin/awg"
        self.h.SYSTEMCTL = "/usr/bin/systemctl"
        for name in ("awg0", "wg1", "wg2"):
            with open(os.path.join(self.dir, name + ".conf"), "w") as fh:
                fh.write("[Interface]\n")
        self.up = ["awg0"]

        def fake_run(cmd, timeout=25):
            if "interfaces" in cmd:
                return (0, " ".join(self.up), "")
            if len(cmd) > 2 and cmd[1] in ("stop", "start"):
                iface = cmd[2].split("@")[1].replace(".service", "")
                if cmd[1] == "stop":
                    self.up = [x for x in self.up if x != iface]
                else:
                    self.up.append(iface)
            return (0, "", "")

        self.h.run = fake_run

    def _switch(self, iface):
        buf, old = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            self.h.action_switch(iface)
        except SystemExit:
            pass
        finally:
            sys.stdout = old
        return json.loads(buf.getvalue())

    def test_switch_stops_previous(self):
        res = self._switch("wg1")
        self.assertEqual(self.up, ["wg1"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["stopped"][0]["iface"], "awg0")

    def test_switch_stops_all_others(self):
        self.up = ["awg0", "wg2"]
        self._switch("wg1")
        self.assertEqual(self.up, ["wg1"])

    def test_switch_idempotent(self):
        self._switch("wg1")
        self._switch("wg1")
        self.assertEqual(self.up, ["wg1"])

    def test_switch_rejects_unknown(self):
        with self.assertRaises(SystemExit):
            buf, old = io.StringIO(), sys.stdout
            sys.stdout = buf
            try:
                self.h.action_switch("../evil")
            finally:
                sys.stdout = old


class SplitTest(unittest.TestCase):
    """Маршрутизация: главное — перекрыть перехват wg-quick."""

    def setUp(self):
        self.sp = load("awg_split", "helper/awg-split")
        d = tempfile.mkdtemp()
        self.sp.STATE_DIR = d
        self.sp.STATE_FILE = os.path.join(d, "split.json")
        self.sp.DIG = None
        self.sp.resolve_all = lambda doms: {x: ["104.18.32.47"] for x in doms}
        self.cmds = []
        self.main_is_tunnel = False

        def fake_run(cmd, check=False, timeout=25):
            self.cmds.append(" ".join(cmd))
            if "-j" in cmd and "route" in cmd:
                return (0, json.dumps([
                    {"dst": "default", "dev": "awg0"},
                    {"dst": "default", "gateway": "192.168.1.1", "dev": "wlp3s0"},
                ]), "")
            if cmd[1] == "route" and "show" in cmd and "main" in cmd:
                return (0, "default dev awg0" if self.main_is_tunnel
                        else "default via 192.168.1.1 dev wlp3s0", "")
            if len(cmd) > 2 and cmd[1] == "rule" and cmd[2] == "del":
                return (1, "", "no such rule")
            return (0, "", "")

        self.sp.run = fake_run

    def added(self, needle):
        return [c for c in self.cmds if "rule add" in c and needle in c]

    # -------------------------------------------------------------- #
    def test_classify_urls_and_nets(self):
        nets, doms, bad = self.sp.classify(
            ["https://chatgpt.com/c/1?x=2", "CHATGPT.com",
             "http://u@site.ru:8080/p", "8.8.8.8", "10.0.0.0/8", "мусор!!"]
        )
        self.assertEqual(nets, ["8.8.8.8/32", "10.0.0.0/8"])
        self.assertEqual(doms, ["chatgpt.com", "site.ru"])
        self.assertEqual(bad, ["мусор!!"])

    def test_rejects_ipv6(self):
        _n, _d, bad = self.sp.classify(["::1/128"])
        self.assertEqual(bad, ["::1/128"])

    def test_include_overrides_wgquick_catch_all(self):
        """Без правила 18500 режим include не действует вовсе."""
        self.sp.apply_split("awg0", "include", ["chatgpt.com", "1.2.3.0/24"])
        self.assertTrue(self.added("to 1.2.3.0/24 table 51821 priority 18000"))
        self.assertTrue(self.added("to 104.18.32.47/32 table 51821"))
        rest = self.added("priority 18500")
        self.assertTrue(rest, "нет правила, перекрывающего перехват wg-quick")
        self.assertIn("table main", rest[0])

    def test_include_uses_direct_table_when_main_hijacked(self):
        self.main_is_tunnel = True
        self.sp.apply_split("awg0", "include", ["chatgpt.com"])
        self.assertIn("table 51822", self.added("priority 18500")[0])

    def test_include_priority_below_wgquick(self):
        self.assertLess(self.sp.PRIO_NETS, 32765)
        self.assertLess(self.sp.PRIO_REST, 32765)

    def test_exclude_sends_listed_direct(self):
        self.sp.apply_split("awg0", "exclude", ["gosuslugi.ru"])
        self.assertTrue(
            self.added("to 104.18.32.47/32 table 51822 priority 18000")
        )
        self.assertFalse(self.added("priority 18500"))

    def test_off_clears_all(self):
        self.sp.apply_split("awg0", "include", ["1.2.3.0/24"])
        self.cmds.clear()
        self.sp.apply_split("awg0", "off", [])
        self.assertTrue(any("rule del priority 18000" in c for c in self.cmds))
        self.assertTrue(any("rule del priority 18500" in c for c in self.cmds))

    def test_reapply_restores(self):
        self.sp.apply_split("awg0", "exclude", ["gosuslugi.ru"])
        self.cmds.clear()
        res = self.sp.reapply("awg0")
        self.assertEqual(res["mode"], "exclude")
        self.assertTrue(self.added("table 51822"))

    def test_reapply_respects_auto_off(self):
        self.sp.apply_split("awg0", "exclude", ["gosuslugi.ru"])
        st = self.sp.load_state()
        st["auto"] = False
        self.sp.save_state(st)
        self.assertTrue(self.sp.reapply("awg0").get("skipped"))

    def test_empty_list_raises(self):
        with self.assertRaises(RuntimeError):
            self.sp.apply_split("awg0", "include", [])

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            self.sp.apply_split("awg0", "чепуха", ["1.2.3.0/24"])

    def test_state_roundtrip(self):
        self.sp.apply_split("awg0", "exclude", ["10.0.0.0/8", "vk.com"])
        st = self.sp.load_state()
        self.assertEqual(st["mode"], "exclude")
        self.assertIn("vk.com", st["entries"])
        self.assertIn("vk.com", st["resolved"])


class PresetsTest(unittest.TestCase):
    def setUp(self):
        # Свой каталог на каждый тест: иначе наборы, созданные в одном
        # тесте, протекают в другой и ломают проверку порядка.
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        for mod in ("awgmanager.settings", "awgmanager.presets"):
            sys.modules.pop(mod, None)
        pkg = sys.modules.get("awgmanager")
        for attr in ("settings", "presets"):
            if pkg is not None and hasattr(pkg, attr):
                delattr(pkg, attr)

        import importlib

        self.p = importlib.import_module("awgmanager.presets")
        self.assertEqual(self.p.load_sets(), {}, "каталог настроек не чист")

    def test_builtin_has_single_russian_set(self):
        names = [s["name"] for s in self.p.all_sets() if s["builtin"]]
        russian = [n for n in names if "Росс" in n]
        self.assertEqual(len(russian), 2)  # IP-набор и доменный
        self.assertIn("Российские сервисы (IP)", names)

    def test_russian_ips_are_valid(self):
        import ipaddress

        for net in self.p.RUSSIAN_IPS:
            ipaddress.ip_network(net, strict=False)

    def test_parse_entries(self):
        good, bad = self.p.parse_entries(
            "https://chatgpt.com\n10.0.0.0/8\n# коммент\nмусор!!\n"
        )
        self.assertEqual(good, ["chatgpt.com", "10.0.0.0/8"])
        self.assertEqual(bad, ["мусор!!"])

    def test_user_set_crud(self):
        self.p.save_set("Мой", ["1.1.1.1/32", "vk.com"], "тест")
        self.assertIn("Мой", [s["name"] for s in self.p.all_sets()])
        self.p.delete_set("Мой")
        self.assertNotIn("Мой", [s["name"] for s in self.p.all_sets()])

    def test_cannot_overwrite_builtin(self):
        with self.assertRaises(ValueError):
            self.p.save_set("Локальные сети (RFC1918)", ["1.1.1.1/32"])

    def test_empty_name_or_entries(self):
        with self.assertRaises(ValueError):
            self.p.save_set("", ["1.1.1.1/32"])
        with self.assertRaises(ValueError):
            self.p.save_set("X", [])

    def test_duplicate_requires_overwrite(self):
        """Случайная перезапись чужого набора недопустима."""
        self.p.save_set("Дубль", ["1.1.1.1/32"], "первый")
        with self.assertRaises(ValueError):
            self.p.save_set("Дубль", ["2.2.2.2/32"])
        self.p.save_set("Дубль", ["2.2.2.2/32"], "второй", overwrite=True)
        self.assertEqual(self.p.load_sets()["Дубль"]["entries"], ["2.2.2.2/32"])
        self.assertEqual(self.p.load_sets()["Дубль"]["description"], "второй")

    def test_name_length_limit(self):
        with self.assertRaises(ValueError):
            self.p.save_set("и" * (self.p.MAX_NAME + 1), ["1.1.1.1/32"])

    def test_name_is_trimmed(self):
        self.p.save_set("  Пробелы  ", ["1.1.1.1/32"])
        self.assertIn("Пробелы", self.p.load_sets())

    def test_rename_flow(self):
        """Переименование = удалить старый + сохранить под новым именем."""
        self.p.save_set("Старое", ["1.1.1.1/32"], "оп")
        self.p.delete_set("Старое")
        self.p.save_set("Новое", ["1.1.1.1/32"], "оп")
        names = self.p.load_sets()
        self.assertNotIn("Старое", names)
        self.assertIn("Новое", names)

    def test_user_sets_listed_before_builtin(self):
        """Свои наборы должны быть видны сразу, а не в конце списка."""
        self.p.save_set("Свой", ["1.1.1.1/32"])
        sets = self.p.all_sets()
        user = [s for s in sets if not s["builtin"]]
        self.assertTrue(user, "пользовательский набор не попал в общий список")
        self.assertEqual(user[0]["name"], "Свой")

    def test_entries_are_copied(self):
        """Набор не должен ссылаться на изменяемый список вызывающего."""
        src = ["1.1.1.1/32"]
        self.p.save_set("Копия", src)
        src.append("9.9.9.9/32")
        self.assertEqual(self.p.load_sets()["Копия"]["entries"], ["1.1.1.1/32"])

    def test_export_import_roundtrip(self):
        entries = ["10.0.0.0/8", "chatgpt.com"]
        text = self.p.export_text(entries, "Тест")
        back, bad = self.p.import_text(text)
        self.assertEqual(back, entries)
        self.assertFalse(bad)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        sys.modules.pop("awgmanager.settings", None)
        from awgmanager import settings as s

        self.mod = s

    def test_defaults(self):
        st = self.mod.Settings()
        self.assertTrue(st.get("exclusive"))
        self.assertTrue(st.get("show_handshake"))

    def test_persist(self):
        st = self.mod.Settings()
        st.set("show_dns", False)
        st.set("interval", 2.5)
        self.assertFalse(self.mod.Settings().get("show_dns"))
        self.assertEqual(self.mod.Settings().get("interval"), 2.5)

    def test_reset(self):
        st = self.mod.Settings()
        st.set("show_dns", False)
        st.reset()
        self.assertTrue(self.mod.Settings().get("show_dns"))

    def test_broken_file(self):
        st = self.mod.Settings()
        st.save()
        with open(self.mod.CONFIG_FILE, "w") as fh:
            fh.write("{ битый json")
        self.assertTrue(self.mod.Settings().get("exclusive"))

    def test_all_stat_keys_declared(self):
        for key, _t, _s in self.mod.STAT_ROWS:
            self.assertIn(key, self.mod.DEFAULTS, key)


class FormatTest(unittest.TestCase):
    def setUp(self):
        from awgmanager import backend

        self.b = backend

    def test_human_bytes(self):
        self.assertEqual(self.b.human_bytes(0), "0 Б")
        self.assertEqual(self.b.human_bytes(1536), "1.5 КиБ")
        self.assertEqual(self.b.human_bytes(None), "—")

    def test_human_ago(self):
        self.assertEqual(self.b.human_ago(0), "никогда")
        now = time.time()
        self.assertIn("мин", self.b.human_ago(now - 300, now))
        self.assertIn("ч", self.b.human_ago(now - 7300, now))


if __name__ == "__main__":
    unittest.main(verbosity=2)
