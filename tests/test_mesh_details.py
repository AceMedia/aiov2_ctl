"""The details block for a node chosen in "Nodes heard": built from the node's record, with nothing invented. No radio,
no Meshtastic library and no Qt are needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_gui import lora_panel as lp  # noqa: E402
from tests.test_mesh_connection import Bus, Link, engine  # noqa: E402

NOW = 1_800_000_000


class NodeDetailsTests(unittest.TestCase):
    FULL = {"user": {"id": "!0000beef", "longName": "Hill Top", "shortName": "HT", "hwModel": "HELTEC_V3",
                     "role": "ROUTER"},
            "lastHeard": NOW - 120, "hopsAway": 0, "snr": 4.0,
            "linkMetrics": {"rxSnr": 6.25, "rxRssi": -91, "time": NOW - 60},
            "deviceMetrics": {"batteryLevel": 80, "voltage": 3.912, "channelUtilization": 12.4, "airUtilTx": 0.42},
            "telemetryReceived": NOW - 300,
            "position": {"latitude": 51.5, "longitude": -0.1, "altitude": 40, "time": NOW - 7200,
                         "precisionBits": 13}}

    def test_everything_the_node_reported_is_shown_with_its_age(self):
        text = "\n".join(lp.node_details(self.FULL, "!0000beef", now=NOW))
        for part in ("Name: Hill Top", "Short name: HT", "Identity: !0000beef", "Hardware: HELTEC_V3", "Role: ROUTER",
                     "Last heard: 2m ago", "SNR +6.2 dB  RSSI -91 dBm (1m ago)", "Hops away: 0 (heard directly)",
                     "Battery: 80%", "Voltage: 3.91 V", "channel 12% busy", "transmitting 0.4% of the time (5m ago)",
                     "Position it sent: 51.50000, -0.10000, 40 m up (position 2h ago)", "13 bits", "2.9 km"):
            self.assertIn(part, text)

    def test_missing_things_are_left_out_not_shown_as_zero(self):
        lines = lp.node_details({"user": {"shortName": "X"}}, "!00000001", now=NOW)
        self.assertEqual(lines, ["Short name: X", "Identity: !00000001"])
        self.assertNotIn("0%", "\n".join(lines))

    def test_external_power_and_full_precision_and_this_radio(self):
        info = {"deviceMetrics": {"batteryLevel": 101},
                "position": {"latitudeI": 515000000, "longitudeI": -1000000, "precisionBits": 32}}
        text = "\n".join(lp.node_details(info, 0xA1B2C3D4, now=NOW, self_id="!a1b2c3d4"))
        self.assertIn("Power: on external power", text)
        self.assertIn("full precision", text)
        self.assertIn("(this radio)", text)
        self.assertIn("position of unknown age", text)
        self.assertNotIn("Battery", text)

    def test_no_position_means_no_position_line_whatever_the_signal(self):
        info = {"linkMetrics": {"rxSnr": 9.0, "rxRssi": -40, "time": NOW}, "position": {}}
        self.assertFalse([l for l in lp.node_details(info, "!1", now=NOW) if "Position" in l])

    def test_precision(self):
        self.assertIsNone(lp.precision_text(None))
        self.assertIsNone(lp.precision_text(0))
        self.assertIn("23.3 km", lp.precision_text(10))
        self.assertIn("728 m", lp.precision_text(15))

    def test_choosing_a_row_and_keeping_it_after_a_rebuild(self):
        ids = ["!a", "!b", "!c"]
        self.assertEqual(lp.picked_node(ids, 1), "!b")
        self.assertIsNone(lp.picked_node(ids, -1))
        self.assertIsNone(lp.picked_node(ids, 3))
        self.assertEqual(lp.row_of(["!c", "!b"], "!b"), 1)
        self.assertEqual(lp.row_of(ids, "!z"), -1)
        self.assertEqual(lp.row_of(ids, None), -1)

    def test_the_engine_notes_when_a_nodes_telemetry_arrived(self):
        bus, link = Bus(), Link()
        e = engine(bus, link)
        bus.send("meshtastic.receive", interface=link, packet={
            "from": 0xBEEF, "fromId": "!0000beef", "id": 5, "rxTime": NOW - 30,
            "decoded": {"portnum": "TELEMETRY_APP", "telemetry": {"deviceMetrics": {"batteryLevel": 70}}}})
        info = e.snapshot()["node_info"]["!0000beef"]
        self.assertEqual(info["telemetryReceived"], NOW - 30)
        self.assertIn("Battery: 70%", lp.node_details(info, "!0000beef", now=NOW))


if __name__ == "__main__":
    unittest.main()
