"""
Tests for --wifi radios and scan: parsing with fixture text, no hardware.

    python3 -m unittest discover -s tests
"""
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402

NMCLI = (
    "18\\:0C\\:7A\\:8F\\:1D\\:81:VM9611446:6:2437 MHz:59:WPA2\n"
    "62\\:DD\\:12\\:2E\\:1F\\:88::1:2412 MHz:47:WPA2\n"
    "AC\\:41\\:6A\\:EB\\:C6\\:04:Cafe\\: Free:36:5180 MHz:20:\n"
    "AA\\:BB\\:CC\\:DD\\:EE\\:01:Corp:11:2462 MHz:100:WPA2 WPA3\n"
    "garbage line\n"
)

IW_DEV = "phy#1\n\tInterface wlan1\n\t\tifindex 6\nphy#0\n\tInterface wlan0\n"


def make_net(base, name, driver, bus, uevent, flags="0x1003"):
    d = os.path.join(base, name)
    os.makedirs(os.path.join(d, "wireless"))
    os.makedirs(os.path.join(d, "device"))
    os.makedirs(os.path.join(base, "_bus", bus), exist_ok=True)
    os.makedirs(os.path.join(base, "_drv", driver), exist_ok=True)
    os.symlink(os.path.join(base, "_drv", driver), os.path.join(d, "device", "driver"))
    os.symlink(os.path.join(base, "_bus", bus), os.path.join(d, "device", "subsystem"))
    with open(os.path.join(d, "device", "uevent"), "w") as f:
        f.write(uevent)
    with open(os.path.join(d, "flags"), "w") as f:
        f.write(flags + "\n")


class ParsingTests(unittest.TestCase):
    def test_nmcli_fields_and_escaping(self):
        nets = a.parse_nmcli_wifi_list(NMCLI, "wlan1")
        self.assertEqual(len(nets), 4)
        self.assertEqual(nets[0], {
            "ssid": "VM9611446", "bssid": "18:0c:7a:8f:1d:81", "channel": 6,
            "freq_mhz": 2437, "signal_dbm": -70, "security": "WPA2", "radio": "wlan1"})
        self.assertEqual(nets[1]["ssid"], "")
        self.assertEqual(nets[2]["ssid"], "Cafe: Free")
        self.assertEqual(nets[2]["security"], "Open")
        self.assertEqual(nets[3]["security"], "WPA2/WPA3")
        self.assertEqual(nets[3]["signal_dbm"], -50)

    def test_nmcli_empty(self):
        self.assertEqual(a.parse_nmcli_wifi_list("", "wlan0"), [])

    def test_iw_dev(self):
        self.assertEqual(a.parse_iw_dev(IW_DEV), ["wlan1", "wlan0"])

    def test_marauder_list(self):
        nets = a.parse_marauder_aps(["[0][CH:6] HomeNet -50", "[1][CH:11] -80", "noise"])
        self.assertEqual(len(nets), 2)
        self.assertEqual(nets[0]["ssid"], "HomeNet")
        self.assertEqual(nets[0]["signal_dbm"], -50)
        self.assertEqual(nets[0]["freq_mhz"], 2437)
        self.assertEqual(nets[0]["radio"], "flipper-marauder")
        self.assertEqual(nets[0]["bssid"], "")

    def test_dedupe_keeps_each_radio(self):
        base = {"ssid": "X", "bssid": "aa:bb:cc:dd:ee:ff", "channel": 6,
                "freq_mhz": 2437, "security": "WPA2"}
        nets = [dict(base, signal_dbm=-70, radio="wlan0"),
                dict(base, signal_dbm=-60, radio="wlan0"),
                dict(base, signal_dbm=-75, radio="wlan1")]
        out = a.dedupe_wifi_networks(nets)
        self.assertEqual([(n["radio"], n["signal_dbm"]) for n in out],
                         [("wlan0", -60), ("wlan1", -75)])


class RadioDetectionTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(a.classify_wifi_radio("sdio", None), "onboard")
        self.assertEqual(a.classify_wifi_radio("usb", ("0e8d", "7961")), "internal-usb")
        self.assertEqual(a.classify_wifi_radio("usb", ("0bda", "8812")), "external-usb")

    def test_detect_from_sysfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_net(tmp, "wlan0", "brcmfmac", "sdio", "DRIVER=brcmfmac\nSDIO_ID=02D0:4345\n")
            make_net(tmp, "wlan1", "mt7921u", "usb",
                     "DRIVER=mt7921u\nPRODUCT=e8d/7961/100\n", flags="0x1002")
            make_net(tmp, "wlan2", "rtl8812au", "usb", "PRODUCT=bda/8812/0\n")
            os.makedirs(os.path.join(tmp, "eth0"))  # not wireless
            radios = a.detect_wifi_radios(tmp, iw_names=[])
        self.assertEqual(radios, [
            {"name": "wlan0", "kind": "onboard", "driver": "brcmfmac", "up": True},
            {"name": "wlan1", "kind": "internal-usb", "driver": "mt7921u", "up": False},
            {"name": "wlan2", "kind": "external-usb", "driver": "rtl8812au", "up": True},
        ])

    def test_no_radios(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(a.detect_wifi_radios(tmp, iw_names=[]), [])


class FlipperSourceTests(unittest.TestCase):
    def test_no_flipper_never_probes(self):
        with mock.patch.object(a, "detect_companions", return_value=[]), \
                mock.patch.object(a, "probe_wifi_board") as probe, \
                mock.patch.object(a, "MarauderSerial") as ser:
            self.assertIsNone(a.scan_wifi_flipper())
        probe.assert_not_called()
        ser.assert_not_called()

    def test_board_silent_is_skipped(self):
        dev = [{"kind": "flipper", "tty": "/dev/ttyACM0"}]
        with mock.patch.object(a, "detect_companions", return_value=dev), \
                mock.patch.object(a, "probe_wifi_board", return_value="not-detected"), \
                mock.patch.object(a, "MarauderSerial") as ser:
            self.assertIsNone(a.scan_wifi_flipper())
        ser.assert_not_called()


class CommandTests(unittest.TestCase):
    def run_cmd(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = a.wifi_command(args)
        return rc, buf.getvalue()

    RADIOS = [{"name": "wlan1", "kind": "internal-usb", "driver": "mt7921u", "up": True}]

    def test_radios_json(self):
        with mock.patch.object(a, "detect_wifi_radios", return_value=self.RADIOS):
            rc, out = self.run_cmd(["radios", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"radios": self.RADIOS})

    def test_scan_json_schema(self):
        nets = a.parse_nmcli_wifi_list(NMCLI, "wlan1")
        with mock.patch.object(a, "detect_wifi_radios", return_value=list(self.RADIOS)), \
                mock.patch.object(a, "scan_wifi_nmcli", return_value=(nets, None)), \
                mock.patch.object(a, "scan_wifi_flipper", return_value=None):
            rc, out = self.run_cmd(["scan", "--json"])
        data = json.loads(out)
        self.assertEqual(set(data), {"radios", "networks", "scanned_at"})
        self.assertEqual(data["radios"], self.RADIOS)
        self.assertEqual(set(data["networks"][0]),
                         {"ssid", "bssid", "channel", "freq_mhz", "signal_dbm",
                          "security", "radio"})
        sig = [n["signal_dbm"] for n in data["networks"]]
        self.assertEqual(sig, sorted(sig, reverse=True))

    def test_scan_text_and_hidden(self):
        nets = a.parse_nmcli_wifi_list(NMCLI, "wlan1")
        with mock.patch.object(a, "detect_wifi_radios", return_value=list(self.RADIOS)), \
                mock.patch.object(a, "scan_wifi_nmcli", return_value=(nets, None)), \
                mock.patch.object(a, "scan_wifi_flipper", return_value=None):
            rc, out = self.run_cmd(["scan"])
        self.assertIn("(hidden)", out)
        self.assertIn("VM9611446", out)

    def test_flipper_added_only_when_present(self):
        ap = a.parse_marauder_aps(["[0][CH:1] Foo -60"])
        with mock.patch.object(a, "detect_wifi_radios", return_value=[]), \
                mock.patch.object(a, "scan_wifi_flipper", return_value=ap):
            data = a.collect_wifi_scan()
        self.assertEqual(data["radios"][0]["kind"], "flipper-marauder")
        self.assertEqual(data["networks"][0]["radio"], "flipper-marauder")

    def test_iface_filter_skips_flipper(self):
        radios = self.RADIOS + [{"name": "wlan0", "kind": "onboard",
                                 "driver": "brcmfmac", "up": True}]
        with mock.patch.object(a, "detect_wifi_radios", return_value=radios), \
                mock.patch.object(a, "scan_wifi_nmcli", return_value=([], None)) as scan, \
                mock.patch.object(a, "scan_wifi_flipper") as fl:
            data = a.collect_wifi_scan("wlan1")
        scan.assert_called_once_with("wlan1")
        fl.assert_not_called()
        self.assertEqual([r["name"] for r in data["radios"]], ["wlan1"])

    def test_bad_usage(self):
        for args in ([], ["bogus"], ["radios", "--iface", "wlan0"], ["scan", "--iface"],
                     ["scan", "--iface", "wl;rm"]):
            rc, out = self.run_cmd(args)
            self.assertEqual(rc, 1, args)
            self.assertIn("Usage", out)

    def test_unknown_iface(self):
        with mock.patch.object(a, "detect_wifi_radios", return_value=self.RADIOS):
            rc, _ = self.run_cmd(["scan", "--iface", "wlan9"])
        self.assertEqual(rc, 1)


class SaveTests(unittest.TestCase):
    def test_private_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "caps")
            data = {"radios": [], "networks": [], "scanned_at": "x"}
            p1 = a.save_wifi_scan(data, target)
            p2 = a.save_wifi_scan(data, target)
            self.assertNotEqual(p1, p2)
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(p1).st_mode), 0o600)
            with open(p1) as f:
                self.assertEqual(json.load(f), data)


class NmcliRunTests(unittest.TestCase):
    def test_falls_back_to_cache_when_rescan_refused(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd[-1])
            if cmd[-1] == "yes":
                return mock.Mock(returncode=1, stdout="", stderr="Scanning not allowed")
            return mock.Mock(returncode=0, stdout=NMCLI, stderr="")

        with mock.patch.object(a.shutil, "which", return_value="/usr/bin/nmcli"), \
                mock.patch.object(a.subprocess, "run", side_effect=fake_run):
            nets, err = a.scan_wifi_nmcli("wlan0")
        self.assertEqual(calls, ["yes", "no"])
        self.assertIsNone(err)
        self.assertEqual(len(nets), 4)

    def test_no_nmcli(self):
        with mock.patch.object(a.shutil, "which", return_value=None):
            nets, err = a.scan_wifi_nmcli("wlan0")
        self.assertEqual(nets, [])
        self.assertIn("nmcli", err)


class FlipperBoardRadioTests(unittest.TestCase):
    FLIPPER = {"kind": "flipper", "tty": "/dev/ttyACM1"}

    def test_listed_when_the_board_answers(self):
        with mock.patch.object(a, "detect_companions", return_value=[self.FLIPPER]),                 mock.patch.object(a, "probe_wifi_board", return_value="present"):
            self.assertEqual(a.flipper_board_radio()["name"], "flipper-marauder")

    def test_nothing_without_a_flipper_and_the_port_is_never_probed(self):
        with mock.patch.object(a, "detect_companions", return_value=[]),                 mock.patch.object(a, "probe_wifi_board") as probe:
            self.assertIsNone(a.flipper_board_radio())
        probe.assert_not_called()

    def test_nothing_when_the_board_does_not_answer(self):
        with mock.patch.object(a, "detect_companions", return_value=[self.FLIPPER]),                 mock.patch.object(a, "probe_wifi_board", return_value="not-detected"):
            self.assertIsNone(a.flipper_board_radio())

    def test_radios_command_includes_it(self):
        out = io.StringIO()
        board = {"name": "flipper-marauder", "kind": "flipper-marauder", "driver": "marauder", "up": True}
        with mock.patch.object(a, "detect_wifi_radios", return_value=[]),                 mock.patch.object(a, "flipper_board_radio", return_value=board),                 redirect_stdout(out):
            self.assertEqual(a.wifi_command(["radios", "--json"]), 0)
        self.assertEqual([r["name"] for r in json.loads(out.getvalue())["radios"]], ["flipper-marauder"])

class MarauderOpenTests(unittest.TestCase):
    def test_open_never_waits_for_output_to_drain(self):
        seen = []
        with mock.patch.object(a.os, "open", return_value=99), \
                mock.patch.object(a.tty, "setraw", side_effect=lambda fd, when=None: seen.append(when)), \
                mock.patch.object(a.termios, "tcgetattr", return_value=[0] * 7), \
                mock.patch.object(a.termios, "tcsetattr"), \
                mock.patch.object(a.fcntl, "ioctl"), \
                mock.patch.object(a.os, "close"):
            ser = a.MarauderSerial("/dev/null")
            ser.open()
            ser.close()
        self.assertEqual(seen, [a.termios.TCSANOW])


if __name__ == "__main__":
    unittest.main()
