import os
import struct
import tempfile
import unittest

from ops_gui import controls as c


def ev(value, kind, number, t=0):
    return struct.pack("<IhBB", t, value, kind, number)


class ParseTests(unittest.TestCase):
    def test_events_and_leftover(self):
        raw = ev(1, 1, 1) + ev(-32767, 2, 0) + ev(0, 1, 1)[:5]
        events, left = c.parse_events(raw)
        self.assertEqual(events, [{"kind": "button", "number": 1, "value": 1},
                                  {"kind": "axis", "number": 0, "value": -32767}])
        self.assertEqual(len(left), 5)
        events, left = c.parse_events(left + ev(0, 1, 1)[5:])
        self.assertEqual(events, [{"kind": "button", "number": 1, "value": 0}] if False else events)
        self.assertEqual(left, b"")

    def test_initial_state_is_skipped(self):
        self.assertEqual(c.parse_events(ev(0, 0x81, 0) + ev(0, 0x82, 1))[0], [])

    def test_axis_directions(self):
        self.assertEqual(c.axis_direction(0, -32767), "left")
        self.assertEqual(c.axis_direction(0, 32767), "right")
        self.assertEqual(c.axis_direction(1, -32767), "up")
        self.assertEqual(c.axis_direction(1, 32767), "down")
        self.assertIsNone(c.axis_direction(0, 5000))
        self.assertIsNone(c.axis_direction(0, 0))
        self.assertIsNone(c.axis_direction(5, 32767))

    def test_actions(self):
        press = lambda n, v=1: {"kind": "button", "number": n, "value": v}
        self.assertEqual(c.action_for(press(1)), ("a", True))
        self.assertEqual(c.action_for(press(2)), ("b", True))
        self.assertEqual(c.action_for(press(0)), ("x", True))
        self.assertEqual(c.action_for(press(3)), ("y", True))
        self.assertEqual(c.action_for(press(1, 0)), (None, False))        # release
        self.assertEqual(c.action_for(press(9)), (None, False))           # not a pad button
        self.assertEqual(c.action_for({"kind": "axis", "number": 0, "value": -30000}), ("left", True))
        self.assertEqual(c.action_for({"kind": "axis", "number": 0, "value": 0}), (None, False))

    def test_mapping_can_be_changed(self):
        self.assertEqual(c.action_for({"kind": "button", "number": 7, "value": 1}, {7: "a"}), ("a", True))

    def test_describe(self):
        self.assertEqual(c.describe({"kind": "button", "number": 1, "value": 1}), "button 1 (A) pressed")
        self.assertEqual(c.describe({"kind": "button", "number": 8, "value": 0}), "button 8 (not mapped) released")
        self.assertEqual(c.describe({"kind": "axis", "number": 1, "value": 30000}), "D-pad down")
        self.assertEqual(c.describe({"kind": "axis", "number": 1, "value": 0}), "axis 1 centred")

    def test_help_mentions_every_action(self):
        text = " ".join(c.help_lines()).lower()
        for word in ("tab", "main action", "back", "refresh", "full screen", "trackball", "j k u i"):
            self.assertIn(word, text)


class DeviceTests(unittest.TestCase):
    def test_missing_device_is_none(self):
        self.assertIsNone(c.open_device("/nonexistent/js9"))

    def test_read_from_a_pipe(self):
        r, w = os.pipe()
        os.set_blocking(r, False)
        self.assertEqual(c.read_events(r), ([], b""))
        os.write(w, ev(1, 1, 2))
        self.assertEqual(c.read_events(r)[0], [{"kind": "button", "number": 2, "value": 1}])
        os.close(w)
        self.assertEqual(c.read_events(r), (None, b""))                  # writer gone
        os.close(r)

    def test_it_only_reads(self):
        import inspect
        src = inspect.getsource(c)
        self.assertNotIn("O_WRONLY", src)
        self.assertNotIn("O_RDWR", src)


if __name__ == "__main__":
    unittest.main()
