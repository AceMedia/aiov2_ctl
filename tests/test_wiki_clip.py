"""The plain parts of the clip tools: tools/wiki-record.py and tools/wiki-clip.py. No screen, no Pillow."""
import importlib.util
import os
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(TOOLS, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


record, clip = load("wiki-record"), load("wiki-clip")


class RecordTests(unittest.TestCase):
    def test_frames_are_numbered_so_they_sort_in_order(self):
        names = [record.frame_name(n) for n in (1, 2, 10, 100)]
        self.assertEqual(names, ["0001.png", "0002.png", "0010.png", "0100.png"])
        self.assertEqual(names, sorted(names))

    def test_a_minute_of_charts_plays_back_in_three_seconds(self):
        count, every, play_ms = record.watch_plan(60, 2, 100)
        self.assertEqual((count, every, play_ms), (30, 2.0, 100))
        self.assertEqual(count * play_ms, 3000)

    def test_a_watch_always_has_a_frame_and_cannot_be_asked_to_hammer_the_screen(self):
        self.assertEqual(record.watch_plan(0, 2, 100)[0], 1)
        self.assertEqual(record.watch_plan(10, 0, 100)[1], 0.2)
        self.assertEqual(record.watch_plan(10, 1, 1)[2], 20)

    def test_the_home_folder_is_written_as_a_tilde(self):
        self.assertEqual(record.home_shortened("/home/pat/aiov2-captures: 3 saved", "/home/pat"), "~/aiov2-captures: 3 saved")
        self.assertEqual(record.home_shortened("Nothing to hide", "/home/pat"), "Nothing to hide")
        self.assertEqual(record.home_shortened("/usr/share", "/"), "/usr/share", "a home of / would mangle every path")

    def test_real_values_are_swapped_for_stand_ins(self):
        swaps = [("192.168.7.31", "192.0.2.10"), ("pats-console", "uconsole")]
        self.assertEqual(record.scrubbed("up 1d / 192.168.7.31", swaps), "up 1d / 192.0.2.10")
        self.assertEqual(record.scrubbed("pats-console: ready", swaps), "uconsole: ready")
        self.assertEqual(record.scrubbed("nothing here", swaps), "nothing here")

    def test_a_value_too_short_to_be_sure_of_is_left_alone(self):
        self.assertEqual(record.scrubbed("pick a pixel", [("pi", "uconsole"), ("", "x"), (None, "y")]), "pick a pixel")

    def test_the_stand_in_address_is_from_the_range_kept_for_examples(self):
        self.assertTrue(record.STAND_IN["address"].startswith("192.0.2."))

    def test_the_network_name_is_replaced_wherever_there_is_one(self):
        got = record.stand_in_network({"wlan0": ("connected", "PatsRouter-5G"), "eth0": ("unavailable", ""), "lo": "odd"},
                                      "HomeNetwork")
        self.assertEqual(got, {"wlan0": ("connected", "HomeNetwork"), "eth0": ("unavailable", ""), "lo": "odd"})

    def test_every_address_is_replaced(self):
        self.assertEqual(record.stand_in_addresses({"wlan0": "192.168.7.31", "eth0": "10.0.0.4"}, "192.0.2.10"),
                         {"wlan0": "192.0.2.10", "eth0": "192.0.2.10"})
        self.assertEqual(record.stand_in_addresses({}, "192.0.2.10"), {})

    def test_the_stand_ins_go_in_before_the_app_starts(self):
        source = open(os.path.join(TOOLS, "wiki-record.py")).read()
        start = source.index("appmod.main()")
        for line in ("statusbar.local_address = lambda", "socket.gethostname = lambda", "pixel.parse_ip_addr = lambda",
                     "pixel.parse_nmcli_dev = lambda"):
            self.assertIn(line, source)
            self.assertLess(source.index(line), start, line)

    def test_a_recording_cannot_save_settings_or_switch_a_rail(self):
        source = open(os.path.join(TOOLS, "wiki-record.py")).read()
        self.assertIn("data.save_settings = lambda *a, **k: None", source)
        self.assertIn("win.toggle_rail = lambda *a, **k: rec.asked_rails.append(a)", source)

    def test_the_clip_scripts_that_come_with_it_each_have_a_clip_function_and_touch_no_rail(self):
        folder = os.path.join(TOOLS, "clips")
        scripts = sorted(n for n in os.listdir(folder) if n.endswith(".py"))
        self.assertGreaterEqual(len(scripts), 3)
        for name in scripts:
            text = open(os.path.join(folder, name)).read()
            self.assertIn("def clip(rec):", text, name)
            for word in ("toggle_rail", "aiov2_ctl", "subprocess", "save_settings"):
                self.assertNotIn(word, text, name)


class PlanTests(unittest.TestCase):
    def test_each_frame_plays_for_what_the_recording_said_and_the_last_is_held(self):
        plan = clip.frame_plan(["0002.png", "0001.png", "0003.png"], {"0001.png": 900, "0002.png": 100, "0003.png": 100})
        self.assertEqual(plan, [("0001.png", 900), ("0002.png", 100), ("0003.png", 1500)])

    def test_a_frame_the_recording_did_not_time_gets_the_usual_length(self):
        self.assertEqual(clip.frame_plan(["0001.png", "0002.png"], {}, hold_last=0), [("0001.png", 400), ("0002.png", 400)])

    def test_one_length_for_every_frame_overrides_the_recording(self):
        plan = clip.frame_plan(["0001.png", "0002.png"], {"0001.png": 900}, hold_last=0, override=50)
        self.assertEqual(plan, [("0001.png", 50), ("0002.png", 50)])

    def test_a_last_frame_already_held_longer_keeps_its_length(self):
        self.assertEqual(clip.frame_plan(["0001.png"], {"0001.png": 4000})[0][1], 4000)

    def test_no_frames_is_an_empty_plan(self):
        self.assertEqual(clip.frame_plan([]), [])

    def test_frames_that_show_the_same_thing_are_folded_into_one(self):
        plan = [("a", 100), ("b", 100), ("c", 100), ("d", 300)]
        same = lambda x, y: {x, y} == {"a", "b"} or {x, y} == {"a", "c"}
        self.assertEqual(clip.folded(plan, same), [("a", 300), ("d", 300)])
        self.assertEqual(clip.folded(plan, lambda x, y: False), plan)
        self.assertEqual(clip.folded([], same), [])


class FitTests(unittest.TestCase):
    def test_the_taskbar_is_cut_off_and_the_picture_scaled_to_the_width_asked_for(self):
        box, size = clip.fit((1280, 720), 800, 24)
        self.assertEqual(box, (0, 0, 1280, 696))
        self.assertEqual(size, (800, 434))

    def test_left_to_itself_a_clip_keeps_its_size(self):
        self.assertEqual(clip.fit((1280, 720)), ((0, 0, 1280, 696), (1280, 696)))

    def test_sizes_are_even_as_video_needs(self):
        for width in (801, 799, 640, 333):
            w, h = clip.fit((1280, 720), width, 24)[1]
            self.assertEqual((w % 2, h % 2), (0, 0), width)

    def test_a_picture_is_never_made_bigger_and_zero_keeps_its_size(self):
        self.assertEqual(clip.fit((1280, 720), 2000, 24)[1], (1280, 696))
        self.assertEqual(clip.fit((1280, 720), 0, 0), ((0, 0, 1280, 720), (1280, 720)))

    def test_the_ending_picks_the_kind_of_file(self):
        self.assertEqual([clip.kind_of(p) for p in ("a.webp", "b.GIF", "c.webm", "d.mp4")], ["webp", "gif", "webm", None])


if __name__ == "__main__":
    unittest.main()
