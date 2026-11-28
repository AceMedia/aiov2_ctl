"""
Tests for the passive survey: frame parsing, the tables, the capture file and the command. No radio needed.

    python3 -m unittest discover -s tests
"""
import inspect
import io
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402

AP = bytes.fromhex("aabbcc001122")
STA = bytes.fromhex("021122334455")
BROADCAST = b"\xff" * 6


def radiotap(signal=-55, freq=2437, flags=0x10):
    present = 1 << 1 | 1 << 2 | 1 << 3 | 1 << 5
    body = bytes([flags, 12]) + struct.pack("<HH", freq, 0x0480) + struct.pack("<b", signal)
    return struct.pack("<BBHI", 0, 0, 8 + len(body), present) + body


def ie(eid, body):
    return bytes([eid, len(body)]) + body


def mgmt(sub, a1, a2, a3, body=b""):
    return bytes([sub << 4, 0]) + b"\x00\x00" + a1 + a2 + a3 + b"\x00\x00" + body


def beacon(ssid=b"HomeNet", channel=6, rsn=None, cap=0x0431, wpa=False):
    body = b"\x00" * 8 + b"\x64\x00" + struct.pack("<H", cap) + ie(0, ssid) + ie(3, bytes([channel]))
    if rsn is not None:
        body += ie(48, rsn)
    if wpa:
        body += ie(221, b"\x00\x50\xf2\x01\x01\x00")
    return mgmt(8, BROADCAST, AP, AP, body)


def rsn(*akms):
    suite = b"\x00\x0f\xac\x04"
    akm = b"".join(b"\x00\x0f\xac" + bytes([x]) for x in akms)
    return b"\x01\x00" + suite + b"\x01\x00" + suite + struct.pack("<H", len(akms)) + akm + b"\x00\x00"


def data(to_ds=True, payload=b"secret payload" * 8):
    fc1 = 1 if to_ds else 2
    a1, a2 = (AP, STA) if to_ds else (STA, AP)
    return bytes([0x08, fc1]) + b"\x00\x00" + a1 + a2 + AP + b"\x00\x00" + payload


def frame(dot11, **kw):
    return radiotap(**kw) + dot11


class SecurityTests(unittest.TestCase):
    def sec(self, **kw):
        s = a.Survey()
        s.see(frame(beacon(**kw)), 1.0)
        return s.aps["aa:bb:cc:00:11:22"]["security"]

    def test_kinds(self):
        self.assertEqual(self.sec(rsn=rsn(2)), "WPA2")
        self.assertEqual(self.sec(rsn=rsn(8)), "WPA3")
        self.assertEqual(self.sec(rsn=rsn(2, 8)), "WPA2/WPA3")
        self.assertEqual(self.sec(rsn=rsn(1)), "WPA2-Enterprise")
        self.assertEqual(self.sec(rsn=rsn(18)), "OWE")
        self.assertEqual(self.sec(wpa=True), "WPA")
        self.assertEqual(self.sec(cap=0x0411), "WEP")
        self.assertEqual(self.sec(cap=0x0401), "Open")

    def test_garbage_security_does_not_crash(self):
        self.assertEqual(a.security_of(0x10, {48: b"\x01"}), "WPA2")


class FrameTests(unittest.TestCase):
    def test_beacon(self):
        s = a.Survey()
        s.see(frame(beacon(), signal=-48, freq=2437), 5.0)
        ap = s.aps["aa:bb:cc:00:11:22"]
        self.assertEqual((ap["ssid"], ap["channel"], ap["beacons"]), ("HomeNet", 6, 1))
        self.assertEqual(s.channels[6]["frames"], 1)

    def test_hidden_ssid_keeps_a_known_name(self):
        s = a.Survey()
        s.see(frame(beacon(ssid=b"")), 1.0)
        self.assertEqual(s.aps["aa:bb:cc:00:11:22"]["ssid"], "")
        s.see(frame(beacon(ssid=b"Shown")), 2.0)
        s.see(frame(beacon(ssid=b"\x00\x00\x00")), 3.0)
        self.assertEqual(s.aps["aa:bb:cc:00:11:22"]["ssid"], "Shown")

    def test_data_both_directions_find_the_station(self):
        for to_ds in (True, False):
            s = a.Survey()
            s.see(frame(data(to_ds)), 1.0)
            self.assertEqual(list(s.stations), ["02:11:22:33:44:55"])
            self.assertEqual(s.stations["02:11:22:33:44:55"]["bssid"], "aa:bb:cc:00:11:22")


    def test_devices_from_control_frames_need_a_known_access_point(self):
        rts = bytes([0xB4, 0, 0, 0]) + AP + STA                       # request to send, access point to device
        back = bytes([0xB4, 0, 0, 0]) + STA + AP                      # and device to access point
        s = a.Survey()
        s.see(frame(rts), 1.0)
        self.assertEqual(s.stations, {})                              # access point not heard yet
        s.see(frame(beacon()), 2.0)
        s.see(frame(rts), 3.0)
        s.see(frame(back), 4.0)
        self.assertEqual(list(s.stations), ["02:11:22:33:44:55"])
        self.assertEqual(s.stations["02:11:22:33:44:55"]["frames"], 2)
        s.see(frame(bytes([0xB4, 0, 0, 0]) + BROADCAST + STA), 5.0)
        self.assertEqual(len(s.stations), 1)

    def test_devices_from_association_and_action_frames(self):
        for sub, src, dst in ((0, STA, AP), (11, AP, STA), (13, STA, AP)):
            s = a.Survey()
            s.see(frame(mgmt(sub, dst, src, AP, b"\x00" * 6)), 1.0)
            self.assertEqual(list(s.stations), ["02:11:22:33:44:55"], sub)
            self.assertEqual(s.stations["02:11:22:33:44:55"]["bssid"], "aa:bb:cc:00:11:22")

    def test_management_frames_between_two_strangers_name_nobody(self):
        other = bytes.fromhex("0a0b0c0d0e0f")
        s = a.Survey()
        s.see(frame(mgmt(13, STA, other, AP, b"\x00" * 6)), 1.0)
        self.assertEqual(s.stations, {})

    def test_probe_requests(self):
        s = a.Survey()
        req = mgmt(4, BROADCAST, STA, BROADCAST, ie(0, b"MyPhoneHotspot"))
        wild = mgmt(4, BROADCAST, STA, BROADCAST, ie(0, b""))
        s.see(frame(req), 1.0)
        s.see(frame(wild), 2.0)
        p = s.probes["02:11:22:33:44:55"]
        self.assertEqual((p["ssids"], p["count"]), (["MyPhoneHotspot"], 2))
        self.assertEqual(s.snapshot(3.0)["probes"][0]["last"], 2.0)

    def test_deauth_flood_needs_a_burst(self):
        s = a.Survey()
        de = mgmt(12, STA, AP, AP, b"\x07\x00")
        for i in range(4):
            s.see(frame(de), 10.0 + i * 0.1)
        self.assertEqual(s.snapshot(11.0)["deauth"], [])
        for i in range(8):
            s.see(frame(de), 11.0 + i * 0.1)
        flood = s.snapshot(12.0)["deauth"]
        self.assertEqual(flood[0]["bssid"], "aa:bb:cc:00:11:22")
        self.assertGreaterEqual(flood[0]["frames"], a.DEAUTH_ALERT)
        self.assertEqual(s.snapshot(30.0)["deauth"], [])          # it ages out

    def test_bad_checksum_and_rubbish_are_ignored(self):
        s = a.Survey()
        s.see(frame(beacon(), flags=0x50), 1.0)
        s.see(b"", 1.0)
        s.see(radiotap() + b"\x80\x00", 1.0)
        s.see(frame(beacon(), signal=0), 1.0)
        self.assertEqual(s.aps, {})

    def test_group_addresses_are_never_stations(self):
        s = a.Survey()
        s.see(frame(mgmt(4, BROADCAST, BROADCAST, BROADCAST, ie(0, b"x"))), 1.0)
        self.assertEqual(s.probes, {})

    def test_snapshot_is_json_and_sorted(self):
        import json
        s = a.Survey()
        s.see(frame(beacon(), signal=-70), 1.0)
        snap = s.snapshot(2.0, channel=6)
        json.dumps(snap)
        self.assertEqual(snap["channel"], 6)
        self.assertEqual(snap["aps"][0]["bssid"], "aa:bb:cc:00:11:22")

    def test_frequency_to_channel(self):
        self.assertEqual([a.freq_to_channel(f) for f in (2412, 2437, 2484, 5180, 5745, 0, None, 9999)],
                         [1, 6, 14, 36, 149, None, None, None])


class CaptureKeepTests(unittest.TestCase):
    """What capture_keep lets into a capture file, from hand-built frames."""

    def eapol_qos(self):
        # QoS data, to the access point, unprotected, LLC/SNAP with ethertype 0x888E, then an EAPOL-Key body
        snap = b"\xaa\xaa\x03\x00\x00\x00\x88\x8e"
        return bytes([0x88, 0x01]) + b"\x00\x00" + AP + STA + AP + b"\x00\x00" + b"\x00\x00" + snap + b"\x02\x03" + b"\x11" * 95

    def test_a_beacon_is_kept_whole(self):
        b = beacon()
        self.assertEqual(a.capture_keep(b), b)

    def test_probes_are_kept_whole(self):
        req = mgmt(4, BROADCAST, STA, BROADCAST, ie(0, b"HomeNet"))
        resp = mgmt(5, STA, AP, AP, b"\x00" * 12 + ie(0, b"HomeNet"))
        self.assertEqual(a.capture_keep(req), req)
        self.assertEqual(a.capture_keep(resp), resp)

    def test_a_data_frame_comes_back_header_only(self):
        d = data()
        self.assertEqual(a.capture_keep(d), d[:24])
        self.assertNotIn(b"secret", a.capture_keep(d))

    def test_a_four_address_qos_data_frame_keeps_its_longer_header_only(self):
        d = bytes([0x88, 0x03]) + b"\x00\x00" + AP + STA + AP + b"\x00\x00" + STA + b"\x00\x00" + b"secret" * 10
        self.assertEqual(a.capture_keep(d), d[:32])

    def test_a_qos_data_frame_carrying_eapol_is_dropped(self):
        self.assertIsNone(a.capture_keep(self.eapol_qos()))

    def test_a_plain_data_frame_carrying_eapol_is_dropped(self):
        d = data(payload=b"\xaa\xaa\x03\x00\x00\x00\x88\x8e" + b"\x01\x03" + b"\x22" * 95)
        self.assertIsNone(a.capture_keep(d))

    def test_an_authentication_frame_comes_back_header_only(self):
        auth = mgmt(11, AP, STA, AP, b"\x03\x00\x01\x00\x00\x00" + b"\x33" * 100)
        self.assertEqual(a.capture_keep(auth), auth[:24])

    def test_association_and_action_frames_come_back_header_only(self):
        for sub in (0, 1, 2, 3, 13):
            f = mgmt(sub, AP, STA, AP, b"\x31\x04\x0a\x00" + ie(0, b"HomeNet") + ie(48, rsn(2)))
            self.assertEqual(a.capture_keep(f), f[:24], sub)

    def test_deauthentication_keeps_only_its_reason_code(self):
        f = mgmt(12, STA, AP, AP, b"\x07\x00" + b"\x44" * 20)
        self.assertEqual(a.capture_keep(f), f[:26])

    def test_truncated_and_malformed_frames_do_not_raise(self):
        e = self.eapol_qos()
        for f in (b"", b"\x08", data()[:20], beacon()[:23], e[:26], e[:28], e[:33], b"\x0c\x00" + b"\x00" * 30,
                  b"\x81\x00" + b"\x00" * 30):
            out = a.capture_keep(f)
            self.assertTrue(out is None or isinstance(out, bytes))
        self.assertIsNone(a.capture_keep(e[:28]))          # the start of an EAPOL header is not kept either
        self.assertIsNone(a.capture_keep(b"\x0c\x00" + b"\x00" * 30))   # extension type
        self.assertIsNone(a.capture_keep(b"\x81\x00" + b"\x00" * 30))   # protocol version not 0

    def test_control_frames_are_kept(self):
        rts = bytes([0xb4, 0]) + b"\x00\x00" + AP + STA
        self.assertEqual(a.capture_keep(rts), rts)


class PcapTests(unittest.TestCase):
    def test_eapol_authentication_and_unreadable_frames_never_reach_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.pcap")
            w = a.PcapWriter(path)
            self.assertFalse(w.write(frame(CaptureKeepTests().eapol_qos()), 1.0))
            self.assertTrue(w.write(frame(mgmt(11, AP, STA, AP, b"SAEsecret" * 5)), 2.0))
            self.assertFalse(w.write(b"\x05" + data(), 3.0))          # no readable radiotap header
            w.close()
            blob = open(path, "rb").read()
            self.assertNotIn(b"\x88\x8e", blob)
            self.assertNotIn(b"SAEsecret", blob)
            self.assertNotIn(b"secret payload", blob)
            self.assertEqual(len(blob), 24 + 16 + len(radiotap()) + 24)

    def test_data_payload_is_cut_and_the_file_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.pcap")
            w = a.PcapWriter(path)
            raw = frame(data())
            w.write(raw, 100.5)
            w.write(frame(beacon()), 101.0)
            w.close()
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            blob = open(path, "rb").read()
            self.assertEqual(struct.unpack_from("<IHHiIII", blob), (0xA1B2C3D4, 2, 4, 0, 0, 4096, 127))
            self.assertNotIn(b"secret payload", blob)
            caplen, origlen = struct.unpack_from("<II", blob, 24 + 8)
            self.assertEqual(origlen, len(raw))
            self.assertLess(caplen, origlen)
            with self.assertRaises(FileExistsError):
                a.PcapWriter(path)


class CommandTests(unittest.TestCase):
    def run_cmd(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = a.survey_command(args)
        return code, out.getvalue() + err.getvalue()

    def test_bad_arguments(self):
        for args in (["--band", "6g"], ["--seconds", "0"], ["--seconds", "x"], ["--iface", "../x"], ["--bogus"], ["--band"]):
            self.assertEqual(self.run_cmd(args)[0], 1, args)

    def test_needs_root(self):
        with mock.patch.object(a.os, "geteuid", return_value=1000):
            code, text = self.run_cmd([])
        self.assertEqual(code, 1)
        self.assertIn("root", text)

    def test_never_transmits(self):
        src = inspect.getsource(a.survey_run) + inspect.getsource(a.Survey) + inspect.getsource(a.parse_dot11)
        for word in (".send(", "sendto", "sendall", "inject", "evil"):
            self.assertNotIn(word, src)

    def test_sudoers_rules_are_exact(self):
        text = a.build_sudoers("shane")
        for band in a.SURVEY_BANDS:
            self.assertIn(f"--wifi-survey --iface wlan1 --band {band} --seconds {a.MONITOR_SECONDS} --json", text)
            self.assertIn(f"--wifi-survey --iface wlan1 --band {band} --seconds {a.MONITOR_SECONDS} --pcap --json", text)
        self.assertNotIn("--wifi-survey *", text)

    def test_the_listener_still_checks_before_touching_the_radio(self):
        done = mock.Mock(stdout="Connected to aa:bb:cc:dd:ee:ff")
        with mock.patch.object(a.os, "geteuid", return_value=0), mock.patch.object(a, "_tool", return_value="/x"), \
                mock.patch.object(a.os.path, "isdir", return_value=True), \
                mock.patch.object(a, "_monitor_cmd", return_value=done) as run:
            code, text = self.run_cmd([])
        self.assertEqual(code, 1)
        self.assertIn("connected", text)
        self.assertFalse([c for c in run.call_args_list if "monitor" in c.args])


if __name__ == "__main__":
    unittest.main()
