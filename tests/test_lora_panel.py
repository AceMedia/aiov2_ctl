"""Pure helpers behind the LoRa tab."""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ops_gui import lora_panel as lp  # noqa: E402


class LoraPanelTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(lp.node_name({"user": {"longName": "Hill Top", "shortName": "HT"}}, "1"), "Hill Top")
        self.assertEqual(lp.node_name({"user": {"shortName": "HT"}}, "1"), "HT")
        self.assertEqual(lp.node_name({}, "!abc"), "!abc")

    def test_age(self):
        self.assertEqual(lp.age_text(None), "never")
        self.assertEqual(lp.age_text(1000, 1030), "now")
        self.assertEqual(lp.age_text(1000, 1000 + 300), "5m ago")
        self.assertEqual(lp.age_text(1000, 1000 + 7200), "2h ago")
        self.assertEqual(lp.age_text(1000, 1000 + 172800), "2d ago")

    def test_a_node_last_heard_months_ago_shows_the_date_not_a_count_of_days(self):
        heard, now = 1768887841, 1795614461                       # the real case: heard 20 Jan, asked 25 Nov
        self.assertEqual(lp.age_text(heard, now), "20 Jan 2026")
        self.assertEqual(lp.age_text(heard, now, dated=False), "309d ago")
        self.assertEqual(lp.age_text(now - 14 * 86400, now), "14d ago")
        self.assertNotIn("ago", lp.age_text(now - 15 * 86400 - 5, now))

    def test_signal(self):
        self.assertEqual(lp.signal_text(6.25, -91), "+6.2 dB  -91 dBm")
        self.assertEqual(lp.signal_text(None), "-")

    def test_rows_put_this_node_first_then_newest(self):
        info = {"1": {"user": {"longName": "Old"}, "lastHeard": 100},
                "2": {"user": {"longName": "New"}, "lastHeard": 900, "deviceMetrics": {"batteryLevel": 80},
                      "hopsAway": 1},
                "3": {"user": {"longName": "Me"}, "lastHeard": 50, "deviceMetrics": {"batteryLevel": 101}}}
        rows = lp.node_rows(info, "3", now=1000)
        self.assertEqual([r["name"] for r in rows], ["Me", "New", "Old"])
        self.assertEqual(rows[0]["battery"], "plugged in")
        self.assertEqual(rows[1]["battery"], "80%")
        self.assertEqual(rows[1]["hops"], "1")
        self.assertTrue(rows[0]["self"])

    def test_activity_line(self):
        line = lp.activity_line({"time": "09:00:01", "channel": 0, "from": "Hill", "summary": "hello",
                                 "rx_snr": 5.5, "sent": False})
        self.assertEqual(line, "09:00:01  ch0  < Hill: hello  (+5.5 dB)")
        self.assertIn(">", lp.activity_line({"from": "Me", "summary": "x", "sent": True}))

    def test_hint_order(self):
        self.assertIn("library", lp.hint(False, False, "on"))
        self.assertIn("rail is off", lp.hint(True, False, "off"))
        self.assertIn("meshtasticd is stopped", lp.hint(True, False, "on"))
        self.assertEqual(lp.hint(True, True, "on"), "")

    def test_sending_needs_everything(self):
        self.assertTrue(lp.can_send(True, True, "hi"))
        self.assertFalse(lp.can_send(False, True, "hi"))
        self.assertFalse(lp.can_send(True, False, "hi"))
        self.assertFalse(lp.can_send(True, True, "   "))

    def test_the_panel_never_sends_on_its_own(self):
        import inspect
        src = inspect.getsource(lp.build_panel)
        self.assertEqual(src.count("send_message("), 1)


if __name__ == "__main__":
    unittest.main()


class RadarTests(unittest.TestCase):
    NODES = {
        "1": {"user": {"longName": "Me"}, "position": {"latitude": 51.0, "longitude": 0.0}},
        "2": {"user": {"longName": "North"}, "position": {"latitude": 51.01, "longitude": 0.0}, "lastHeard": 1000},
        "3": {"user": {"longName": "East"}, "position": {"latitudeI": 510000000, "longitudeI": 100000}, "lastHeard": 99999},
        "4": {"user": {"longName": "NoPos"}},
        "5": {"user": {"longName": "Null"}, "position": {"latitude": 0, "longitude": 0}},
    }

    def test_distance_and_bearing(self):
        self.assertAlmostEqual(lp.haversine_km(51, 0, 51.01, 0), 1.112, places=2)
        self.assertAlmostEqual(lp.bearing_deg(51, 0, 51.01, 0), 0, places=1)
        self.assertAlmostEqual(lp.bearing_deg(51, 0, 51, 0.01), 90, delta=0.2)

    def test_positions(self):
        self.assertEqual(lp.node_latlon(self.NODES["3"]), (51.0, 0.01))
        for bad in ("4", "5"):
            self.assertIsNone(lp.node_latlon(self.NODES[bad]))
        self.assertIsNone(lp.node_latlon(None))
        self.assertEqual(lp.own_position(self.NODES, "1"), (51.0, 0.0))
        self.assertIsNone(lp.own_position(self.NODES, None))
        self.assertIsNone(lp.own_position({}, "1"))
        # the engine may report self_id as an int while node_info keys its entries as strings (or the other way
        # round); own_position must find the match either way, not only when the types already agree
        self.assertEqual(lp.own_position(self.NODES, 1), (51.0, 0.0))
        self.assertEqual(lp.own_position({1: self.NODES["1"]}, "1"), (51.0, 0.0))

    def test_distance_text(self):
        self.assertEqual(lp.distance_text(None), "-")
        self.assertEqual(lp.distance_text(0.25), "250 m")
        self.assertEqual(lp.distance_text(3.14), "3.1 km")

    def test_radar_points(self):
        pts, span = lp.radar_points(self.NODES, "1", (51.0, 0.0), 1.0, now=100000)
        self.assertEqual([p["name"] for p in pts], ["East", "North"])          # nearest first
        north = next(p for p in pts if p["name"] == "North")
        self.assertGreater(north["y"], 0)
        self.assertAlmostEqual(north["x"], 0, places=3)
        self.assertFalse(north["inside"])                  # 1.11 km is outside a 1 km ring
        self.assertTrue(north["stale"])
        east = next(p for p in pts if p["name"] == "East")
        self.assertTrue(east["inside"] is True and not east["stale"])
        self.assertGreater(east["x"], 0)
        self.assertGreaterEqual(span, 2.0)                 # at least twice the ring
        self.assertTrue(all(abs(p["x"]) <= 1 and abs(p["y"]) <= 1 for p in pts))

    def test_radar_without_own_position(self):
        self.assertEqual(lp.radar_points(self.NODES, "1", None, 1.0)[0], [])

    def test_rows_get_a_distance(self):
        rows = lp.node_rows(self.NODES, "1", own=(51.0, 0.0))
        by = {r["name"]: r["distance"] for r in rows}
        self.assertEqual(by["Me"], "-")
        self.assertEqual(by["North"], "1.1 km")
        self.assertEqual(by["NoPos"], "-")
        self.assertEqual(lp.node_rows(self.NODES, "1")[0]["distance"], "-")

    def test_channels(self):
        snap = {"active_channels": [{"channel": 2, "count": 5}, {"channel": 0, "count": 1}], "selected_channel": 3}
        self.assertEqual([c for c, _ in lp.channel_choices(snap)], [0, 2, 3])
        self.assertEqual(lp.channel_choices({}), [(0, "Channel 0")])

    def test_alerts(self):
        lines = lp.alert_lines([{"time": "10:00:00", "message": "A entered"}, {"time": "10:01:00", "message": "B left"}],
                               [("!abc", 3600)])
        self.assertEqual(lines[0], "10:01:00  B left")
        self.assertIn("!abc", lines[2])
        named = lp.alert_lines(None, [("7", 7200)], {"7": {"user": {"longName": "Hilltop"}}})
        self.assertEqual(named, ["Not heard for 2h: Hilltop"])
        self.assertEqual(lp.alert_lines(None, None), [])

    def test_still_never_sends_without_the_switch(self):
        import inspect
        src = inspect.getsource(lp)
        self.assertEqual(src.count("send_message("), 1)


class MessageTests(unittest.TestCase):
    ACTS = [
        {"time": "10:00:00", "from": "Ann", "kind": "text", "summary": "hello", "channel": 0, "sent": False, "rx_snr": 5.0},
        {"time": "10:01:00", "from": "Me", "kind": "text", "summary": "hi back", "channel": 0, "sent": True},
        {"time": "10:02:00", "from": "Bob", "kind": "checkin", "summary": "Check-in 51.5,-0.1", "channel": 1, "sent": False},
        {"time": "10:03:00", "from": "Bob", "kind": "telemetry", "summary": "Telemetry 90%", "channel": 1, "sent": False},
        "junk",
    ]

    def test_only_messages_and_checkins(self):
        rows = lp.message_rows(self.ACTS)
        self.assertEqual([r["text"] for r in rows], ["hello", "hi back", "Check-in 51.5,-0.1"])
        self.assertEqual([r["who"] for r in rows], ["Ann", "Me", "Bob"])
        self.assertTrue(rows[1]["sent"] and rows[2]["checkin"])

    def test_channel_filter_and_limit(self):
        self.assertEqual([r["who"] for r in lp.message_rows(self.ACTS, channel=1)], ["Bob"])
        self.assertEqual(len(lp.message_rows(self.ACTS, limit=2)), 2)
        self.assertEqual(lp.message_rows(None), [])
        self.assertEqual(lp.message_channels(self.ACTS), [0, 1])

    def test_unread_ignores_what_we_sent(self):
        rows = lp.message_rows(self.ACTS)
        self.assertEqual(lp.unread_count(rows, None), 2)                                 # nothing read yet
        self.assertEqual(lp.unread_count(rows, lp.row_signature(rows[0])), 1)            # read up to the first
        self.assertEqual(lp.unread_count(rows, lp.row_signature(rows[-1])), 0)           # read up to the last
        self.assertEqual(lp.unread_count([], None), 0)

    def test_unread_does_not_get_stuck_once_the_row_window_fills_up(self):
        # message_rows keeps only the newest `limit` rows, so once that cap is reached the *count* of incoming
        # messages in the window stays the same even as genuinely new ones arrive and old ones fall off the front.
        # Counting by identity instead must still see the new arrival as unread.
        acts = [{"kind": "text", "time": f"t{i}", "from": "Ann", "summary": f"m{i}"} for i in range(5)]
        rows = lp.message_rows(acts, limit=3)                 # the newest 3: m2, m3, m4
        mark = lp.row_signature(rows[-1])                     # read up to m4 (the end of the old window)
        self.assertEqual(lp.unread_count(rows, mark), 0)
        acts.append({"kind": "text", "time": "t5", "from": "Ann", "summary": "m5"})
        rows2 = lp.message_rows(acts, limit=3)                 # the newest 3: m3, m4, m5 (m2 fell off the front)
        self.assertEqual(len(rows2), len(rows))                # the count alone could not tell these apart...
        self.assertEqual(lp.unread_count(rows2, mark), 1)      # ...but the new message (m5) is still found unread

    def test_unread_falls_back_to_treating_everything_as_unread_once_the_mark_itself_scrolls_out(self):
        acts = [{"kind": "text", "time": f"t{i}", "from": "Ann", "summary": f"m{i}"} for i in range(5)]
        mark = lp.row_signature(lp.message_rows(acts, limit=5)[0])          # read up to the very first message
        rows = lp.message_rows(acts, limit=3)                               # a window that no longer contains it
        self.assertEqual(lp.unread_count(rows, mark), len(rows))            # safer than getting stuck at 0


class ColumnWidthTests(unittest.TestCase):
    NEEDS = [0, 90, 70, 80, 80, 50]                   # name, signal, distance, heard, battery, hops

    def test_the_name_gets_the_spare_width(self):
        widths = lp.column_widths(800, self.NEEDS)
        self.assertEqual(widths[1:], self.NEEDS[1:])
        self.assertEqual(widths[0], 800 - sum(self.NEEDS[1:]))
        self.assertEqual(sum(widths), 800)

    def test_a_narrow_table_keeps_the_name_readable(self):
        widths = lp.column_widths(400, self.NEEDS)
        self.assertGreaterEqual(widths[0], lp.NAME_MIN_PX)
        self.assertEqual(sum(widths), 400)
        self.assertTrue(all(w < n for w, n in zip(widths[1:], self.NEEDS[1:])))

    def test_odd_input(self):
        self.assertEqual(lp.column_widths(500, []), [])
        self.assertEqual(lp.column_widths(-5, [0, 10]), [0, 0])
        self.assertEqual(lp.column_widths(100, [0, 0]), [100, 0])
