"""
Tests for the read only --doctor checks. Each check takes plain text, so no
uConsole is needed.

    python3 -m unittest discover -s tests
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402

GOOD_CONFIG = """
[all]
dtoverlay=spi1-1cs
[pi5]
dtoverlay=i2c-rtc,pcf85063a,i2c_csi_dsi0
dtparam=rtc=off
"""


def levels(results):
    return [l for l, _ in results]


def text(results):
    return " ".join(m for _, m in results)


class RtcTests(unittest.TestCase):
    def test_working_setup(self):
        r = a.check_rtc(GOOD_CONFIG, ["rtc-pcf85063 10-0051"], "")
        self.assertEqual(levels(r), ["ok"])

    def test_rtc_off_without_overlay_means_no_clock(self):
        r = a.check_rtc("dtparam=rtc=off\n", [], "")
        self.assertIn("warn", levels(r))
        self.assertIn("i2c-rtc", text(r))

    def test_commented_lines_are_ignored(self):
        r = a.check_rtc("#dtoverlay=i2c-rtc,pcf85063a\ndtparam=rtc=off\n", [], "")
        self.assertIn("warn", levels(r))

    def test_overlay_but_no_clock_registered(self):
        r = a.check_rtc(GOOD_CONFIG, [], "")
        self.assertIn("warn", levels(r))

    def test_boot_messages_seen_on_the_uconsole_are_info_only(self):
        d = ("rtc-pcf85063 10-0051: POR issue detected, sending a SW reset\n"
             "rtc-pcf85063 10-0051: hctosys: unable to read the hardware clock\n")
        r = a.check_rtc(GOOD_CONFIG, ["rtc-pcf85063 10-0051"], d)
        self.assertNotIn("warn", levels(r))
        self.assertIn("CR1220", text(r))


class KernelTests(unittest.TestCase):
    def test_either_package_name_is_accepted(self):
        for name in ("clockworkpi-kernel", "akrex-kernel"):
            self.assertEqual(levels(a.check_kernel({name}, "")), ["ok", "ok"])

    def test_missing_kernel_package(self):
        r = a.check_kernel(set(), "")
        self.assertEqual(levels(r)[0], "warn")
        self.assertIn("akrex-kernel", text(r))

    def test_upgrade_that_removes_the_kernel_is_flagged(self):
        sim = "Inst bash [1] (2)\nRemv clockworkpi-kernel [6.12.57] []\n"
        r = a.check_kernel({"clockworkpi-kernel"}, sim)
        self.assertIn("warn", levels(r))
        self.assertIn("clockworkpi-kernel", text(r))

    def test_ordinary_upgrade_is_fine(self):
        sim = "Inst libc6 [1] (2)\nRemv oldlib [1]\n"
        self.assertNotIn("warn", levels(a.check_kernel({"akrex-kernel"}, sim)))

    def test_no_simulation_available(self):
        self.assertEqual(levels(a.check_kernel({"akrex-kernel"}, None)), ["ok"])


class EepromTests(unittest.TestCase):
    def test_current_bootloader(self):
        self.assertEqual(levels(a.check_eeprom("2025/12/08 19:29:54\nversion x")), ["ok"])

    def test_old_bootloader(self):
        self.assertEqual(levels(a.check_eeprom("2024/11/20 10:00:00")), ["warn"])

    def test_unreadable(self):
        self.assertEqual(levels(a.check_eeprom(None)), ["info"])


class ConfigTests(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(levels(a.check_config(GOOD_CONFIG)), ["ok"])

    def test_spi_on_is_flagged(self):
        r = a.check_config("dtparam=spi=on\n")
        self.assertEqual(levels(r), ["warn"])
        self.assertIn("backlight", text(r))

    def test_spi_overlay_is_not(self):
        self.assertEqual(levels(a.check_config("dtoverlay=spi1-1cs\n")), ["ok"])

    def test_cm3_only_and_dither(self):
        r = a.check_config("gpio=11=op,dh\ndisable_audio_dither=1\n")
        self.assertEqual(levels(r), ["info", "info"])


class OtherChecks(unittest.TestCase):
    def test_alsa(self):
        self.assertEqual(levels(a.check_alsa("1.2.14-1+rpt1")), ["info"])
        self.assertEqual(a.check_alsa("1.2.14-2"), [])
        self.assertEqual(a.check_alsa(""), [])

    def test_battery_gauge_disagreement(self):
        r = a.check_battery({"capacity": 100, "voltage": 2.7}, 0)
        self.assertIn("warn", levels(r))

    def test_healthy_battery_and_power(self):
        r = a.check_battery({"capacity": 90, "voltage": 4.1}, 0)
        self.assertEqual(levels(r), ["ok", "ok"])

    def test_no_reading_and_throttling(self):
        r = a.check_battery(None, 0x50005)
        self.assertEqual(levels(r), ["warn", "warn"])

    def test_storage(self):
        self.assertEqual(levels(a.check_storage("rw,noatime", "")), ["ok"])
        self.assertIn("warn", levels(a.check_storage("ro,noatime", "")))
        bad = "EXT4-fs error (device mmcblk0p2): ext4_validate_block_bitmap:423: bad block bitmap checksum"
        self.assertIn("warn", levels(a.check_storage("rw", bad)))


class BootRailTests(unittest.TestCase):
    SYSTEM_OFF = {"rails_on_boot": {"GPS": False, "LORA": False, "SDR": False, "USB": False}}

    def test_no_user_entry_is_fine(self):
        for user in (None, {}, {"mesh_on_boot": False}):
            self.assertEqual(levels(a.check_boot_rails(self.SYSTEM_OFF, user)), ["ok"])

    def test_agreeing_files_are_fine(self):
        r = a.check_boot_rails(self.SYSTEM_OFF, self.SYSTEM_OFF)
        self.assertEqual(levels(r), ["ok"])
        self.assertIn("agree", text(r))

    def test_stale_user_entry_is_reported_but_not_alarming(self):
        # What was found on the uConsole: user file says LORA and SDR on, system file all off.
        user = {"rails_on_boot": {"GPS": False, "LORA": True, "SDR": True, "USB": False}}
        r = a.check_boot_rails(self.SYSTEM_OFF, user)
        self.assertEqual(levels(r), ["info"])
        self.assertIn("LORA on", text(r))
        self.assertIn("SDR on", text(r))
        self.assertIn("system config", text(r))
        self.assertNotIn("GPS", text(r).split("says")[1].split("but")[0])

    def test_missing_system_config_counts_as_all_off(self):
        user = {"rails_on_boot": {"USB": True}}
        self.assertEqual(levels(a.check_boot_rails(None, user)), ["info"])

    def test_it_never_writes_either_file(self):
        import inspect
        src = inspect.getsource(a.check_boot_rails)
        for word in ("open(", "write", "remove", "save_config"):
            self.assertNotIn(word, src)


class TrayTests(unittest.TestCase):
    OLD = "[Desktop Entry]\nExec=/usr/bin/python3 /usr/local/bin/aiov2_ctl --gui\nXDG_AUTOSTART_DELAY=5\n"

    def test_no_autostart_says_nothing(self):
        self.assertEqual(a.check_tray_autostart(None, False), [])

    def test_old_entry_is_flagged_with_the_fix(self):
        r = a.check_tray_autostart(self.OLD, False)
        self.assertEqual(levels(r), ["warn"])
        self.assertIn("aiov2_ctl --autostart", text(r))

    def test_current_entry(self):
        self.assertEqual(levels(a.check_tray_autostart(a.AUTOSTART_DESKTOP, True)), ["ok"])
        self.assertEqual(levels(a.check_tray_autostart(a.AUTOSTART_DESKTOP, False)), ["info"])

    def test_someone_elses_file_is_not_called_stale(self):
        self.assertFalse(a.autostart_is_stale("[Desktop Entry]\nExec=other\n"))

    def test_refresh_rewrites_only_a_stale_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "aiov2_ctl.desktop")
            with open(path, "w") as f:
                f.write(self.OLD)
            with mock.patch.object(a, "autostart_path", return_value=path), \
                    mock.patch.object(a.os, "geteuid", return_value=1000), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(a.enable_autostart(), 0)
                self.assertEqual(open(path).read(), a.AUTOSTART_DESKTOP)
            other = "[Desktop Entry]\nExec=other\n"
            with open(path, "w") as f:
                f.write(other)
            with mock.patch.object(a, "autostart_path", return_value=path), \
                    mock.patch.object(a.os, "geteuid", return_value=1000), \
                    redirect_stdout(io.StringIO()):
                a.enable_autostart()
            self.assertEqual(open(path).read(), other)


class MeshTests(unittest.TestCase):
    def test_not_set_up_says_nothing(self):
        self.assertEqual(a.check_mesh(False, False, "off", False), [])
        self.assertEqual(a.check_mesh(True, True, None, True), [])

    def test_working(self):
        self.assertEqual(levels(a.check_mesh(True, True, "on", True)), ["ok"])

    def test_rail_on_but_service_stopped(self):
        r = a.check_mesh(True, False, "on", True)
        self.assertEqual(levels(r), ["warn"])
        self.assertIn("systemctl start meshtasticd", text(r))

    def test_service_running_without_power(self):
        r = a.check_mesh(True, True, "off", True)
        self.assertEqual(levels(r), ["warn"])
        self.assertIn("LORA on", text(r))

    def test_everything_off_is_only_info(self):
        self.assertEqual(levels(a.check_mesh(True, False, "off", True)), ["info"])

    def test_missing_spi_node(self):
        r = a.check_mesh(True, True, "on", False)
        self.assertIn("spi1-1cs", text(r))


class CommandTests(unittest.TestCase):
    def test_runs_end_to_end_and_never_writes(self):
        out = io.StringIO()
        with mock.patch.object(a, "_doctor_cmd", return_value=None), \
                mock.patch.object(a.Telemetry, "power_summary", return_value=None), \
                mock.patch.object(a, "DOCTOR_CONFIG_PATH", "/nonexistent"), \
                mock.patch("builtins.open", side_effect=OSError), \
                redirect_stdout(out):
            self.assertEqual(a.doctor_command(), 0)
        self.assertIn("read only", out.getvalue())

    def test_commands_it_runs_are_all_reads(self):
        import inspect
        src = inspect.getsource(a.doctor_command)
        for word in ("sudo", "rm ", "write", "tee", "install", "reboot"):
            self.assertNotIn(word, src)



IW_PHY_MONITOR = """Wiphy phy0
	wiphy index: 0
	max # scan SSIDs: 4
	Supported interface modes:
		 * managed
		 * AP
		 * P2P-client
		 * P2P-GO
		 * monitor
	Band 1:
		Capabilities: 0x1ff
"""

IW_PHY_NO_MONITOR = """Wiphy phy1
	Supported interface modes:
		 * IBSS
		 * managed
		 * AP
	software interface modes (can always be added):
		 * monitor
	Band 1:
"""

IW_LINK_CONNECTED = """Connected to aa:bb:cc:dd:ee:ff (on wlan1)
	SSID: HomeNet
	freq: 2437
	RX: 1234 bytes (10 packets)
	signal: -51 dBm
"""


class WifiMonitorTests(unittest.TestCase):
    def test_parse_modes(self):
        self.assertEqual(a.parse_iw_modes(IW_PHY_MONITOR), ["managed", "ap", "p2p-client", "p2p-go", "monitor"])
        self.assertEqual(a.parse_iw_modes(IW_PHY_NO_MONITOR), ["ibss", "managed", "ap"])   # software list ignored
        self.assertIsNone(a.parse_iw_modes(""))
        self.assertIsNone(a.parse_iw_modes(None))
        self.assertIsNone(a.parse_iw_modes("command failed: No such device (-19)"))

    def test_parse_link(self):
        self.assertEqual(a.parse_iw_link_freq(IW_LINK_CONNECTED), 2437)
        self.assertIsNone(a.parse_iw_link_freq("Not connected."))
        self.assertIsNone(a.parse_iw_link_freq(None))
        self.assertEqual(a.parse_iw_link_freq("Connected to aa:bb:cc:dd:ee:ff (on wlan1)"), 0)

    def test_card_absent_is_not_an_error(self):
        out = a.check_wifi_monitor(None, "", "", False, None, None)
        self.assertEqual([l for l, _ in out], ["info"])
        self.assertIn("not present", out[0][1])

    def test_no_iw(self):
        out = a.check_wifi_monitor("wlan1", "mt7921u", "6.12.25", False, None, None)
        self.assertEqual(out[0][0], "info")
        self.assertIn("'iw' is not installed", out[0][1])

    def test_monitor_advertised(self):
        out = a.check_wifi_monitor("wlan1", "mt7921u", "6.12.25-v8+", True, IW_PHY_MONITOR, "Not connected.")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "ok")
        for word in ("mt7921u", "6.12.25-v8+", "advertises monitor mode"):
            self.assertIn(word, out[0][1])

    def test_monitor_not_advertised(self):
        out = a.check_wifi_monitor("wlan1", "mt7921u", "6.12", True, IW_PHY_NO_MONITOR, "Not connected.")
        self.assertEqual(out[0][0], "warn")

    def test_unreadable_modes(self):
        self.assertEqual(a.check_wifi_monitor("wlan1", "", "6.12", True, None, None)[0][0], "info")

    def test_connected_says_listening_follows_the_channel(self):
        out = a.check_wifi_monitor("wlan1", "mt7921u", "6.12", True, IW_PHY_MONITOR, IW_LINK_CONNECTED)
        self.assertEqual([l for l, _ in out], ["ok", "info"])
        self.assertIn("channel 6", out[1][1])
        self.assertIn("follows that channel", out[1][1])

    def test_gathering_changes_nothing(self):
        calls = []
        radios = [{"name": "wlan1", "kind": "internal-usb", "driver": "mt7921u", "up": True}]
        with mock.patch.object(a, "detect_wifi_radios", return_value=radios), \
                mock.patch.object(a, "_tool", return_value="/usr/sbin/iw"), \
                mock.patch.object(a, "read_sysfs", return_value="phy0\n"), \
                mock.patch.object(a, "_doctor_cmd", side_effect=lambda args, timeout=10: calls.append(args) or
                                  (IW_PHY_MONITOR if "phy" in args else "Not connected.")), \
                mock.patch.object(a, "_monitor_cmd") as changer:
            out = a._doctor_wifi_monitor()
        self.assertEqual(out[0][0], "ok")
        self.assertEqual(calls, [["/usr/sbin/iw", "phy", "phy0", "info"], ["/usr/sbin/iw", "dev", "wlan1", "link"]])
        changer.assert_not_called()

    def test_gathering_with_the_card_off(self):
        with mock.patch.object(a, "detect_wifi_radios", return_value=[]), \
                mock.patch.object(a, "_doctor_cmd") as run:
            out = a._doctor_wifi_monitor()
        self.assertIn("not present", out[0][1])
        run.assert_not_called()

if __name__ == "__main__":
    unittest.main()
