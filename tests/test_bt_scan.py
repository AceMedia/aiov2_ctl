"""
Tests for the passive Bluetooth scan: the command packets, report parsing, the device table and the command.
No radio needed.

    python3 -m unittest discover -s tests
"""
import io
import os
import struct
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402

ADDR = bytes.fromhex("665544332211")            # on the wire, last byte first: 11:22:33:44:55:66


def ad(*fields):
    return b"".join(bytes([len(body) + 1, kind]) + body for kind, body in fields)


NAME = ad((0x01, b"\x06"), (0x09, b"Kitchen Scale"), (0xFF, struct.pack("<H", 0x004C) + b"\x01\x02"),
          (0x03, struct.pack("<HH", 0x180F, 0x181D)), (0x0A, struct.pack("<b", -4)))


def legacy_event(reports):
    body = bytes([0x02, len(reports)])
    for event_type, addr_type, addr, data, rssi in reports:
        body += bytes([event_type, addr_type]) + addr + bytes([len(data)]) + data + struct.pack("<b", rssi)
    return bytes([0x04, 0x3E, len(body)]) + body


def ext_event(reports):
    body = bytes([0x0D, len(reports)])
    for event_type, addr_type, addr, data, rssi in reports:
        body += (struct.pack("<HB", event_type, addr_type) + addr + bytes([1, 0, 0xFF]) + struct.pack("<bb", 127, rssi)
                 + struct.pack("<H", 0) + bytes([0]) + bytes(6) + bytes([len(data)]) + data)
    return bytes([0x04, 0x3E, len(body)]) + body


class PassiveOnlyTests(unittest.TestCase):
    """The one rule that matters: nothing here may make the radio transmit."""

    def test_both_forms_ask_for_the_passive_scan_type(self):
        legacy = a.bt_scan_commands(False, True)[0]
        self.assertEqual(struct.unpack_from("<H", legacy, 1)[0], a.BT_OP_SCAN_PARAMS)
        self.assertEqual(legacy[4], 0x00)                                  # scan type: passive
        ext = a.bt_scan_commands(True, True)[0]
        self.assertEqual(struct.unpack_from("<H", ext, 1)[0], a.BT_OP_EXT_SCAN_PARAMS)
        self.assertEqual(ext[7], 0x00)                                     # scan type for the one PHY: passive
        self.assertEqual(a.BT_SCAN_PASSIVE, 0x00)

    def test_only_scan_commands_are_ever_built(self):
        allowed = {a.BT_OP_SCAN_PARAMS, a.BT_OP_SCAN_ENABLE, a.BT_OP_EXT_SCAN_PARAMS, a.BT_OP_EXT_SCAN_ENABLE}
        for extended in (True, False):
            for enable in (True, False):
                for packet in a.bt_scan_commands(extended, enable):
                    self.assertEqual(packet[0], 0x01)
                    self.assertIn(struct.unpack_from("<H", packet, 1)[0], allowed)
                    self.assertEqual(packet[3], len(packet) - 4)           # the length byte is right

    def test_the_source_has_no_connect_pair_or_advertise_commands(self):
        import inspect
        src = "".join(inspect.getsource(f) for f in (a.bt_scan_run, a.bt_scan_commands, a.bt_command))
        for opcode in ("0x200D", "0x2043", "0x2006", "0x2008", "0x200A", "0x2039", "0x0405"):   # connect, advertise, inquiry page
            self.assertNotIn(opcode.lower(), src.lower())

    def test_switching_off_sends_only_the_enable_command_with_enable_clear(self):
        for extended in (True, False):
            packets = a.bt_scan_commands(extended, False)
            self.assertEqual(len(packets), 1)
            self.assertEqual(packets[0][4], 0)


class ParsingTests(unittest.TestCase):
    def test_an_advertisement_gives_name_maker_services_and_power(self):
        out = a.bt_parse_ad(NAME)
        self.assertEqual(out["name"], "Kitchen Scale")
        self.assertEqual(out["company"], 0x004C)
        self.assertEqual(out["services"], [0x180F, 0x181D])
        self.assertEqual(out["tx_power"], -4)

    def test_broken_or_empty_data_is_survived(self):
        self.assertEqual(a.bt_parse_ad(b""), {})
        self.assertEqual(a.bt_parse_ad(b"\x09\x09abc"), {})                 # says 9 bytes, has 4
        self.assertEqual(a.bt_parse_ad(b"\x00\x00\x00"), {})

    def test_a_name_cannot_smuggle_control_characters(self):
        out = a.bt_parse_ad(ad((0x09, b"bad\x1b[31mname\n")))
        self.assertTrue(out["name"].isprintable())

    def test_a_legacy_report(self):
        ev = a.bt_event(legacy_event([(0x00, 0x00, ADDR, NAME, -61)]))
        self.assertEqual(ev[0], "reports")
        (r,) = ev[1]
        self.assertEqual((r["addr"], r["kind"], r["rssi"], r["connectable"]), ("11:22:33:44:55:66", "public", -61, True))
        self.assertEqual(r["name"], "Kitchen Scale")

    def test_two_reports_in_one_event(self):
        other = bytes.fromhex("0102030405c6")
        ev = a.bt_event(legacy_event([(0x03, 0x01, other, b"", -80), (0x00, 0x00, ADDR, NAME, -50)]))
        self.assertEqual([r["addr"] for r in ev[1]], ["c6:05:04:03:02:01", "11:22:33:44:55:66"])
        self.assertFalse(ev[1][0]["connectable"])

    def test_an_extended_report(self):
        (r,) = a.bt_event(ext_event([(0x0013, 0x01, bytes.fromhex("010203040546"), NAME, -72)]))[1]
        self.assertEqual((r["addr"], r["rssi"], r["connectable"]), ("46:05:04:03:02:01", -72, True))
        self.assertEqual(r["kind"], "private (changes)")
        self.assertEqual(r["name"], "Kitchen Scale")

    def test_a_cut_off_report_is_dropped_not_guessed_at(self):
        whole = legacy_event([(0x00, 0x00, ADDR, NAME, -61)])
        cut = whole[:3] + whole[3:-6]
        cut = bytes([cut[0], cut[1], len(cut) - 3]) + cut[3:]
        self.assertEqual(a.bt_event(cut), ("reports", []))

    def test_no_reading_is_none_not_a_number(self):
        (r,) = a.bt_event(legacy_event([(0x00, 0x00, ADDR, b"", 127)]))[1]
        self.assertIsNone(r["rssi"])

    def test_command_answers(self):
        complete = bytes([0x04, 0x0E, 4, 1]) + struct.pack("<H", a.BT_OP_SCAN_ENABLE) + bytes([0x0C])
        self.assertEqual(a.bt_event(complete), ("done", a.BT_OP_SCAN_ENABLE, 0x0C))
        status = bytes([0x04, 0x0F, 4, 0x01, 1]) + struct.pack("<H", a.BT_OP_EXT_SCAN_PARAMS)
        self.assertEqual(a.bt_event(status), ("done", a.BT_OP_EXT_SCAN_PARAMS, 0x01))
        self.assertIsNone(a.bt_event(b"\x02\x00\x00"))
        self.assertIsNone(a.bt_event(b""))

    def test_address_kinds(self):
        self.assertEqual(a.bt_addr_kind(0, "11:22:33:44:55:66"), "public")
        self.assertEqual(a.bt_addr_kind(1, "c6:05:04:03:02:01"), "random (fixed)")
        self.assertEqual(a.bt_addr_kind(1, "46:05:04:03:02:01"), "private (changes)")
        self.assertEqual(a.bt_addr_kind(1, "06:05:04:03:02:01"), "private (changes)")


class TableTests(unittest.TestCase):
    def report(self, **kw):
        base = {"addr": "11:22:33:44:55:66", "kind": "public", "connectable": True, "rssi": -60}
        base.update(kw)
        return base

    def test_a_device_is_counted_and_its_best_signal_kept(self):
        scan = a.BtScan()
        scan.add(self.report(rssi=-70), 10.0)
        dev = scan.add(self.report(rssi=-55, name="Scale"), 12.0)
        scan.add(self.report(rssi=-80), 13.0)
        self.assertEqual((dev["count"], dev["rssi"], dev["best"], dev["first"], dev["last"]), (3, -80, -55, 10.0, 13.0))
        self.assertEqual(dev["name"], "Scale")                             # a name once heard is kept

    def test_maker_comes_from_the_company_number_or_a_public_address(self):
        scan = a.BtScan(vendor=lambda mac: "Acme Corp")
        self.assertEqual(scan.add(self.report(company=0x004C), 1.0)["maker"], "Apple")
        self.assertEqual(scan.add(self.report(addr="aa:bb:cc:00:00:01", company=0xABCD), 1.0)["maker"], "company 0xabcd")
        self.assertEqual(scan.add(self.report(addr="aa:bb:cc:00:00:02"), 1.0)["maker"], "Acme Corp")

    def test_a_private_address_is_never_given_a_maker_from_its_address(self):
        scan = a.BtScan(vendor=lambda mac: "Acme Corp")
        dev = scan.add(self.report(addr="46:05:04:03:02:01", kind="private (changes)"), 1.0)
        self.assertEqual(dev["maker"], "")

    def test_services_are_named_when_known(self):
        dev = a.BtScan().add(self.report(services=[0x180D, 0x1234]), 1.0)
        self.assertEqual(dev["services"], ["heart rate", "0x1234"])

    def test_the_snapshot_lists_the_strongest_first(self):
        scan = a.BtScan()
        scan.add(self.report(addr="aa:00:00:00:00:01", rssi=-80), 1.0)
        scan.add(self.report(addr="aa:00:00:00:00:02", rssi=-40), 1.0)
        scan.add(self.report(addr="aa:00:00:00:00:03", rssi=None), 1.0)
        snap = scan.snapshot(2.0, "hci1", "extended")
        self.assertEqual([d["addr"][-1] for d in snap["devices"]], ["2", "1", "3"])
        self.assertEqual((snap["adapter"], snap["mode"]), ("hci1", "extended"))


class CommandTests(unittest.TestCase):
    def run_cmd(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = a.bt_command(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_bad_arguments_print_the_usage_and_start_nothing(self):
        with mock.patch.object(a, "bt_scan_run") as run:
            for args in (("--adapter", "wlan1"), ("--adapter", "hci1; rm"), ("--seconds", "0"),
                         ("--seconds", "99999"), ("--seconds", "soon"), ("--connect", "x"), ("--adapter",)):
                code, out, _ = self.run_cmd(*args)
                self.assertEqual(code, 1, args)
                self.assertIn("Usage", out)
            run.assert_not_called()

    def test_it_needs_root(self):
        with mock.patch.object(a.os, "geteuid", return_value=1000):
            code, _, err = self.run_cmd("--seconds", "5")
        self.assertEqual(code, 1)
        self.assertIn("needs root", err)

    def test_json_mode_prints_each_snapshot(self):
        snap = {"t": 1.0, "adapter": "hci1", "mode": "extended", "devices": []}
        with mock.patch.object(a, "bt_scan_run", side_effect=lambda adapter, seconds, emit: emit(snap)):
            code, out, _ = self.run_cmd("--json")
        self.assertEqual(code, 0)
        self.assertEqual(a.json.loads(out), snap)

    def test_the_sudoers_rule_is_the_exact_command_the_app_runs(self):
        rules = a.build_sudoers("shane")
        self.assertIn(f"{a.INSTALLED_PATH} --bt-scan --adapter hci1 --seconds {a.BT_SECONDS} --json", rules)
        self.assertEqual(rules.count("--bt-scan"), 1)


if __name__ == "__main__":
    unittest.main()
