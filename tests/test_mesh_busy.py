"""How busy the channel is, as this radio's own report of itself, with its age. No radio is needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_gui import lora_panel as lp  # noqa: E402
from tests.test_mesh_connection import Bus, Link, engine  # noqa: E402

NOW = 1_800_000_000
ME = "!a1b2c3d4"


class ChannelBusyTests(unittest.TestCase):
    def test_both_figures_with_age(self):
        nodes = {ME: {"deviceMetrics": {"channelUtilization": 12.3, "airUtilTx": 0.42}, "telemetryReceived": NOW - 120}}
        self.assertEqual(lp.channel_busy_text(nodes, 0xA1B2C3D4, NOW),
                         "This radio reports the channel 12% busy and has been transmitting 0.4% of the time, "
                         "as of 2m ago")

    def test_one_figure_and_an_unknown_age(self):
        nodes = {ME: {"deviceMetrics": {"airUtilTx": 1.25}}}
        self.assertEqual(lp.channel_busy_text(nodes, ME, NOW),
                         "This radio reports that it has been transmitting 1.2% of the time "
                         "(when it reported this is not known)")
        nodes = {ME: {"deviceMetrics": {"channelUtilization": 3}, "telemetryReceived": NOW - 5}}
        self.assertEqual(lp.channel_busy_text(nodes, ME, NOW),
                         "This radio reports the channel 3.0% busy, as of just now")

    def test_not_reported_is_a_dash_and_other_nodes_do_not_count(self):
        other = {"!0000beef": {"deviceMetrics": {"channelUtilization": 50, "airUtilTx": 5}}}
        self.assertEqual(lp.channel_busy_text(other, ME, NOW), "-")
        self.assertEqual(lp.channel_busy_text({ME: {"deviceMetrics": {"batteryLevel": 90}}}, ME, NOW), "-")
        self.assertEqual(lp.channel_busy_text(other, None, NOW), "-")
        self.assertEqual(lp.channel_busy_text(None, ME, NOW), "-")

    def test_from_the_radios_own_telemetry_packet(self):
        bus, link = Bus(), Link()
        e = engine(bus, link)
        bus.send("meshtastic.receive", interface=link, packet={
            "from": 0xA1B2C3D4, "fromId": ME, "id": 3, "rxTime": NOW - 60,
            "decoded": {"portnum": "TELEMETRY_APP",
                        "telemetry": {"deviceMetrics": {"channelUtilization": 20.0, "airUtilTx": 2.0}}}})
        snap = e.snapshot()
        self.assertEqual(lp.channel_busy_text(snap["node_info"], snap["self_id"], NOW),
                         "This radio reports the channel 20% busy and has been transmitting 2.0% of the time, "
                         "as of 1m ago")


if __name__ == "__main__":
    unittest.main()
