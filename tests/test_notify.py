import unittest

from ops_gui import notify as n


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class ConfigTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        c = n.config({})
        self.assertTrue(c["toasts"] and c["mesh"] and c["gps"] and c["warnings"])
        self.assertFalse(c["system"])                       # desktop notifications are opt in
        self.assertFalse(c["rails"])
        c = n.config({"notifications": {"system": True, "mesh": False, "bogus": True, "gps": "yes"}})
        self.assertTrue(c["system"])
        self.assertFalse(c["mesh"])
        self.assertTrue(c["gps"])                           # a non boolean value is ignored
        self.assertNotIn("bogus", c)
        self.assertEqual(n.config({"notifications": 5}), n.DEFAULTS)

    def test_every_source_has_a_default(self):
        for key, _label in n.SOURCES:
            self.assertIn(key, n.DEFAULTS)


class CentreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = n.Center(clock=self.clock)
        self.heard = []
        self.c.listeners.append(self.heard.append)

    def test_a_notification_reaches_listeners_and_history(self):
        e = self.c.add("mesh", "Ann", "hello", "info")
        self.assertEqual((e["title"], e["body"]), ("Ann", "hello"))
        self.assertEqual(len(self.heard), 1)
        self.assertEqual(len(self.c.history), 1)

    def test_a_switched_off_source_is_held_back(self):
        self.assertIsNone(self.c.add("nodes", "New node heard", "Skynet"))      # off by default
        self.c.cfg["nodes"] = True
        self.assertIsNotNone(self.c.add("nodes", "New node heard", "Skynet"))
        self.assertEqual(len(self.heard), 1)

    def test_repeats_are_dropped(self):
        self.c.add("mesh", "Ann", "hello")
        self.assertIsNone(self.c.add("mesh", "Ann", "hello"))
        self.clock.t += 6
        self.assertIsNotNone(self.c.add("mesh", "Ann", "hello"))
        self.assertIsNotNone(self.c.add("mesh", "Ann", "different"))

    def test_bursts_are_folded(self):
        for i in range(6):
            self.c.add("mesh", f"Node {i}", "hi")
        self.assertEqual(len(self.heard), 4)
        self.assertEqual(len(self.c.history), 6)            # still kept in the history
        self.clock.t += 11
        self.c.add("mesh", "Later", "hi")
        self.assertEqual(self.heard[-1]["folded"], 2)       # the toast says how many it skipped

    def test_blank_titles_and_long_text(self):
        self.assertIsNone(self.c.add("mesh", "   ", "x"))
        e = self.c.add("mesh", "T" * 100, "b" * 400)
        self.assertEqual((len(e["title"]), len(e["body"])), (40, 160))
        self.assertTrue(e["title"].endswith("~"))

    def test_odd_levels_and_a_broken_listener(self):
        self.c.listeners.insert(0, lambda e: 1 / 0)
        self.assertEqual(self.c.add("mesh", "A", "b", "purple")["level"], "info")
        self.assertEqual(len(self.heard), 1)

    def test_history_is_bounded(self):
        for i in range(100):
            self.clock.t += 20
            self.c.add("mesh", f"n{i}", "x")
        self.assertEqual(len(self.c.history), n.HISTORY)


class WatcherTests(unittest.TestCase):
    def test_gps_events(self):
        w = n.GpsWatcher()
        self.assertIsNone(w.update(None))
        self.assertIsNone(w.update(1))                      # no fix yet, nothing to say
        self.assertEqual(w.update(3)[0], "GPS fix gained")
        self.assertIsNone(w.update(3))
        self.assertEqual(w.update(1)[0], "GPS fix lost")
        self.assertEqual(w.update(2)[2], "good")
        self.assertEqual(n.GpsWatcher().update(3)[0], "GPS fix")        # already fixed when we started

    def test_warnings_fire_once_then_again_after_clearing(self):
        w = n.WarningWatcher()
        self.assertEqual(w.update(pct=50, temp=50), [])
        self.assertEqual(w.update(pct=14)[0][0], "Battery 14%")
        self.assertEqual(w.update(pct=13), [])
        self.assertEqual(w.update(pct=40), [])
        self.assertEqual(len(w.update(pct=10)), 1)
        self.assertEqual(w.update(pct=5, charging=True), [])            # charging is not a warning
        self.assertEqual(w.update(temp=85)[0][2], "hot")
        self.assertEqual(w.update(temp=85), [])
        self.assertEqual(w.update(throttled="under voltage")[0][0], "Power warning")
        self.assertEqual(w.update(pct=6)[0][2], "hot")


class MeshTests(unittest.TestCase):
    def act(self, summary, kind="text", who="Ann", sent=False, t="10:00:00", uid="!a"):
        return {"time": t, "from": who, "from_id": uid, "summary": summary, "kind": kind, "sent": sent}

    def test_the_first_look_announces_nothing(self):
        events, seen = n.mesh_events([self.act("old")], None)
        self.assertEqual(events, [])
        events, seen = n.mesh_events([self.act("old"), self.act("new", t="10:01:00")], seen)
        self.assertEqual(events, [("mesh", "Ann", "new", "info")])

    def test_nothing_is_announced_twice(self):
        _e, seen = n.mesh_events([], None)
        acts = [self.act("hi")]
        e1, seen = n.mesh_events(acts, seen)
        e2, seen = n.mesh_events(acts, seen)
        self.assertEqual((len(e1), len(e2)), (1, 0))

    def test_our_own_messages_are_never_announced(self):
        _e, seen = n.mesh_events([], None)
        events, _s = n.mesh_events([self.act("mine", sent=True, who="Me")], seen)
        self.assertEqual(events, [])

    def test_checkins_and_new_nodes(self):
        _e, seen = n.mesh_events([], None)
        known = {"!old"}
        acts = [self.act("Check-in 51.5,-0.1", kind="checkin"), self.act("Identity SKY", kind="user", who="Skynet", uid="!new", t="x"),
                self.act("Identity OLD", kind="user", who="Old", uid="!old", t="y"), self.act("t", kind="telemetry", t="z")]
        events, _s = n.mesh_events(acts, seen, known)
        self.assertEqual([(e[0], e[1]) for e in events], [("mesh", "Ann checked in"), ("nodes", "New node heard")])
        self.assertIn("!new", known)

    def test_the_seen_set_stays_bounded(self):
        _e, seen = n.mesh_events([], None)
        acts = [self.act(f"m{i}", t=str(i)) for i in range(1000)]
        _e, seen = n.mesh_events(acts, seen)
        self.assertLessEqual(len(seen), 400)


class SystemTests(unittest.TestCase):
    def test_arguments(self):
        self.assertEqual(n.system_args("Ann", "hi", "info"), ("Ann", "hi", 0, 8000))
        self.assertEqual(n.system_args("Low", "x", "hot"), ("Low", "x", 2, 0))
        self.assertEqual(n.system_args("Low", "x", "warn")[2], 1)
        self.assertEqual(len(n.system_args("T" * 100, "b" * 500, "info")[0]), 60)


if __name__ == "__main__":
    unittest.main()
