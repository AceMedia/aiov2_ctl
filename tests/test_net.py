"""
Tests for the wired network command: link facts, address and neighbour parsing, frame parsing, the host table,
and above all the limits that keep the active parts to your own private network.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


def mac(text):
    return bytes(int(x, 16) for x in text.split(":"))


def arp_frame(op, smac, sip, tmac="00:00:00:00:00:00", tip="192.168.0.1"):
    body = struct.pack("!HHBBH", 1, 0x0800, 6, 4, op) + mac(smac) + bytes(map(int, sip.split("."))) + \
        mac(tmac) + bytes(map(int, tip.split(".")))
    return mac("ff:ff:ff:ff:ff:ff") + mac(smac) + b"\x08\x06" + body


def udp_frame(smac, sip, sport, dport, payload):
    udp = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    ip = struct.pack("!BBHHHBBH", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0) + bytes(map(int, sip.split("."))) + b"\xe0\x00\x00\xfb"
    return mac("01:00:5e:00:00:fb") + mac(smac) + b"\x08\x00" + ip + udp


class LinkTests(unittest.TestCase):
    def make(self, d, name, type_="1", wireless=False, virtual=False, carrier="1", speed="1000", duplex="full"):
        real = os.path.join(d, "devices", "virtual" if virtual else "platform", name)
        os.makedirs(real, exist_ok=True)
        for k, v in (("type", type_), ("carrier", carrier), ("speed", speed), ("duplex", duplex), ("operstate", "up"),
                     ("address", "aa:bb:cc:00:11:22")):
            with open(os.path.join(real, k), "w") as f:
                f.write(v)
        if wireless:
            os.makedirs(os.path.join(real, "wireless"))
        os.makedirs(os.path.join(d, "sys"), exist_ok=True)
        os.symlink(real, os.path.join(d, "sys", name))

    def test_only_real_wired_interfaces(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d, "eth0")
            self.make(d, "wlan0", wireless=True)
            self.make(d, "docker0", virtual=True)
            self.make(d, "lo", type_="772")
            self.assertEqual(a.ethernet_interfaces(os.path.join(d, "sys")), ["eth0"])
            self.assertEqual(a.ethernet_interfaces(os.path.join(d, "none")), [])

    def test_link_info(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d, "eth0")
            info = a.link_info("eth0", os.path.join(d, "sys"))
            self.assertEqual((info["carrier"], info["speed_mbps"], info["duplex"], info["mac"]), (True, 1000, "full", "aa:bb:cc:00:11:22"))
            self.make(d, "eth1", carrier="0", speed="-1")
            down = a.link_info("eth1", os.path.join(d, "sys"))
            self.assertEqual((down["carrier"], down["speed_mbps"], down["duplex"]), (False, None, None))


class ParseTests(unittest.TestCase):
    def test_addresses_and_neighbours(self):
        self.assertEqual(a.parse_ip_addr4("2: eth0    inet 192.168.1.5/24 brd 192.168.1.255 scope global eth0"), [("192.168.1.5", 24)])
        self.assertEqual(a.parse_ip_addr4(""), [])
        rows = a.parse_ip_neigh("192.168.0.1 dev eth0 lladdr 18:0C:7A:8F:1D:80 REACHABLE\n192.168.0.9 dev eth0  FAILED\njunk")
        self.assertEqual(rows, [{"ip": "192.168.0.1", "mac": "18:0c:7a:8f:1d:80", "state": "REACHABLE", "dev": "eth0"}])

    def test_vendor_lookup(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "oui.txt")
            with open(path, "w") as f:
                f.write("18-0C-7A   (hex)\t\tCompal Broadband Networks\n180C7A     (base 16)\t\tCompal\n")
            self.assertEqual(a.oui_vendor("18:0c:7a:8f:1d:80", path), "Compal Broadband Networks")
            self.assertEqual(a.oui_vendor("00:00:00:00:00:00", path), "")
            self.assertEqual(a.oui_vendor("18:0c:7a", os.path.join(d, "none")), "")

    def test_ports_text(self):
        self.assertEqual(a.parse_ports("22,80,8000-8002"), [22, 80, 8000, 8001, 8002])
        for bad in ("", "0", "70000", "abc", "1-500", "22,,x"):
            self.assertIsNone(a.parse_ports(bad), bad)


class FrameTests(unittest.TestCase):
    def test_arp(self):
        obs = a.parse_frame(arp_frame(2, "aa:bb:cc:00:00:01", "192.168.0.7"))
        self.assertEqual((obs["mac"], obs["ip"], obs["proto"]), ("aa:bb:cc:00:00:01", "192.168.0.7", "arp"))
        self.assertIsNone(a.parse_frame(arp_frame(1, "aa:bb:cc:00:00:01", "0.0.0.0")))       # an address probe says nothing

    def test_mdns_announcement(self):
        name = b"\x08mybox-pi\x05local\x00"
        payload = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0) + name + struct.pack("!HHIH", 1, 1, 120, 4) + b"\xc0\xa8\x00\x07"
        obs = a.parse_frame(udp_frame("aa:bb:cc:00:00:02", "192.168.0.8", 5353, 5353, payload))
        self.assertEqual((obs["proto"], obs["name"], obs["ip"]), ("mdns", "mybox-pi.local", "192.168.0.8"))

    def test_dhcp_host_name(self):
        bootp = b"\x01\x01\x06\x00" + b"\x00" * 232 + b"\x63\x82\x53\x63" + b"\x35\x01\x03" + b"\x0c\x06laptop" + b"\xff"
        obs = a.parse_frame(udp_frame("aa:bb:cc:00:00:03", "0.0.0.0", 68, 67, bootp))
        self.assertEqual((obs["proto"], obs["name"], obs["ip"]), ("dhcp", "laptop", ""))

    def test_lldp_name(self):
        tlv = lambda kind, data: struct.pack("!H", (kind << 9) | len(data)) + data
        body = tlv(1, b"\x04" + mac("aa:bb:cc:00:00:04")) + tlv(5, b"core-switch") + tlv(0, b"")
        frame = mac("01:80:c2:00:00:0e") + mac("aa:bb:cc:00:00:04") + b"\x88\xcc" + body
        obs = a.parse_frame(frame)
        self.assertEqual((obs["proto"], obs["name"]), ("lldp", "core-switch"))

    def test_garbage_never_raises(self):
        for raw in (b"", b"\x00" * 5, b"\xff" * 60, os.urandom(200)):
            a.parse_frame(raw)
        self.assertIsNone(a.parse_frame(b"\x00" * 13))

    def test_a_looping_dns_pointer_ends(self):
        loop = struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0) + b"\xc0\x0c"
        a.parse_frame(udp_frame("aa:bb:cc:00:00:02", "192.168.0.8", 5353, 5353, loop))


class TableTests(unittest.TestCase):
    def test_one_host_many_observations(self):
        t = a.HostTable(["11:22:33:44:55:66"])
        t.see({"mac": "aa:bb:cc:00:00:01", "ip": "192.168.0.7", "name": "", "proto": "arp"})
        t.see({"mac": "aa:bb:cc:00:00:01", "ip": "", "name": "mybox", "proto": "mdns"})
        t.see({"mac": "aa:bb:cc:00:00:01", "ip": "192.168.0.7", "name": "mybox", "proto": "arp"})
        t.see({"mac": "11:22:33:44:55:66", "ip": "192.168.0.2", "name": "", "proto": "arp"})     # ourselves
        t.see({"mac": "ff:ff:ff:ff:ff:ff", "ip": "", "name": "", "proto": "ip"})
        t.see(None)
        rows = t.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["ip"], rows[0]["name"], rows[0]["via"], rows[0]["packets"]), ("192.168.0.7", "mybox", "arp, mdns", 3))

    def test_rows_are_sorted_by_address(self):
        t = a.HostTable()
        for i, ip in enumerate(("192.168.0.20", "192.168.0.3", "")):
            t.see({"mac": f"aa:00:00:00:00:0{i}", "ip": ip, "name": "", "proto": "arp"})
        self.assertEqual([r["ip"] for r in t.rows()], ["192.168.0.3", "192.168.0.20", ""])


class LimitTests(unittest.TestCase):
    def test_private_addresses_only(self):
        for ok in ("10.1.2.3", "172.16.0.9", "172.31.255.1", "192.168.0.1", "169.254.1.1"):
            self.assertTrue(a.is_private_ip(ok), ok)
        for bad in ("8.8.8.8", "172.32.0.1", "1.2.3.4", "127.0.0.1", "::1", "not an ip", "", None):
            self.assertFalse(a.is_private_ip(bad), bad)

    def test_sweep_targets_are_bounded(self):
        t = a.sweep_targets("192.168.0.83", 24)
        self.assertEqual((len(t), t[0], t[-1]), (253, "192.168.0.1", "192.168.0.254"))
        self.assertNotIn("192.168.0.83", t)
        self.assertEqual(len(a.sweep_targets("10.0.0.5", 22)), 1021)
        self.assertEqual(a.sweep_targets("10.0.0.5", 16), [])                 # too big
        self.assertEqual(a.sweep_targets("10.0.0.5", 8), [])
        self.assertEqual(a.sweep_targets("8.8.8.8", 24), [])                  # not private
        self.assertEqual(a.sweep_targets("1.2.3.4", 30), [])

    def test_a_host_must_be_in_our_own_subnet(self):
        import ipaddress
        ours = [ipaddress.ip_network("192.168.0.0/24")]
        self.assertTrue(a.host_in_our_subnets("192.168.0.9", ours))
        for bad in ("192.168.1.9", "8.8.8.8", "10.0.0.1", "garbage"):
            self.assertFalse(a.host_in_our_subnets(bad, ours), bad)
        self.assertFalse(a.host_in_our_subnets("192.168.0.9", []))

    def test_the_arp_request_is_a_plain_who_has(self):
        f = a.arp_request("aa:bb:cc:00:00:01", "192.168.0.83", "192.168.0.9")
        self.assertEqual(len(f), 42)
        self.assertEqual(f[:6], b"\xff" * 6)
        self.assertEqual(f[12:14], b"\x08\x06")
        self.assertEqual(struct.unpack("!H", f[20:22])[0], 1)                  # a request
        self.assertEqual(f[38:42], bytes([192, 168, 0, 9]))

    def test_the_active_commands_need_yes(self):
        for argv in (["sweep"], ["ports", "192.168.0.9"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch.object(a, "socket") as sock:
                self.assertEqual(a.net_command(argv), 1)
            self.assertIn("--yes", out.getvalue())
            sock.socket.assert_not_called()                                    # nothing was even opened

    def test_ports_refuse_other_networks(self):
        with mock.patch.object(a, "ethernet_interfaces", return_value=["eth0"]), \
                mock.patch.object(a, "_net_iface_addrs", return_value=[("192.168.0.83", 24)]), \
                mock.patch.object(a, "net_ports") as probe, contextlib.redirect_stdout(io.StringIO()) as out:
            for host in ("8.8.8.8", "192.168.5.5", "example.com"):
                self.assertEqual(a.net_command(["ports", host, "--yes"]), 1)
            probe.assert_not_called()
            probe.return_value = [22]
            self.assertEqual(a.net_command(["ports", "192.168.0.9", "--yes", "--ports", "22,80"]), 0)
            probe.assert_called_once_with("192.168.0.9", [22, 80])

    def test_usage_and_bad_arguments(self):
        for argv in ([], ["bogus"], ["listen", "--seconds", "x"], ["listen", "--seconds", "9999"], ["status", "extra"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(a.net_command(argv), 1, argv)

    def test_listening_never_sends(self):
        import inspect
        src = inspect.getsource(a.net_listen)
        for word in (".send(", "sendto", "sendall"):
            self.assertNotIn(word, src)

    def test_status_is_only_reads(self):
        import inspect
        src = inspect.getsource(a.net_status) + inspect.getsource(a.net_neighbors)
        for word in ("socket", "sudo", "arping", "nmap", "ping"):
            self.assertNotIn(word, src)


class StatusTests(unittest.TestCase):
    def test_json_status_without_a_cable(self):
        info = {"name": "eth0", "state": "down", "carrier": False, "speed_mbps": None, "duplex": None, "mac": "aa", "driver": "macb"}
        with mock.patch.object(a, "ethernet_interfaces", return_value=["eth0"]), mock.patch.object(a, "link_info", return_value=info), \
                mock.patch.object(a, "_net_iface_addrs", return_value=[]), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(a.net_command(["status", "--json"]), 0)
        import json
        data = json.loads(out.getvalue())
        self.assertFalse(data["interfaces"][0]["carrier"])
        self.assertIn("Plug a cable", data["interfaces"][0]["hint"])

    def test_picking_an_interface_needs_a_link(self):
        with mock.patch.object(a, "ethernet_interfaces", return_value=["eth0"]), \
                mock.patch.object(a, "link_info", return_value={"carrier": False}):
            with self.assertRaises(OSError):
                a._pick_iface(None)
            with self.assertRaises(OSError):
                a._pick_iface("wlan0")
            self.assertEqual(a._pick_iface("eth0"), "eth0")


if __name__ == "__main__":
    unittest.main()
