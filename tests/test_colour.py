import unittest

from ops_gui import colour as c, pixel, skin, style


class HexTests(unittest.TestCase):
    def test_forms_are_normalised(self):
        for text, want in (("#D70A53", "#d70a53"), ("d70a53", "#d70a53"), ("#fa0", "#ffaa00"), ("0F8", "#00ff88"),
                           ("  #123456 ", "#123456")):
            self.assertEqual(c.valid_hex(text), want)

    def test_rubbish_is_refused(self):
        for text in ("", None, "#12", "#12345", "#1234567", "ggg", "#gg0000", "red", "#12 456"):
            self.assertIsNone(c.valid_hex(text))

    def test_hsv_round_trips(self):
        for hx in ("#d70a53", "#e95420", "#62e06b", "#ffffff", "#000000", "#4c8dff", "#808080"):
            h, s, v = c.hex_to_hsv(hx)
            back = c.hsv_to_hex(h, s, v)
            self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(c.to_rgb(hx), c.to_rgb(back))), (hx, back))

    def test_known_hues(self):
        self.assertEqual(c.hsv_to_hex(0, 1, 1), "#ff0000")
        self.assertEqual(c.hsv_to_hex(120, 1, 1), "#00ff00")
        self.assertEqual(c.hsv_to_hex(240, 1, 1), "#0000ff")
        self.assertEqual(c.hsv_to_hex(360, 1, 1), "#ff0000")
        self.assertEqual(c.hsv_to_hex(0, 0, 0.5), "#808080")

    def test_out_of_range_is_clamped(self):
        self.assertEqual(c.hsv_to_hex(10, 5, 5), c.hsv_to_hex(10, 1, 1))
        self.assertEqual(c.hsv_to_hex(10, -1, -1), "#000000")


class ReadableTests(unittest.TestCase):
    def test_a_black_pick_is_lightened_so_the_interface_stays_usable(self):
        self.assertGreaterEqual(c.luminance(c.readable("#000000")), 0.06)
        self.assertGreaterEqual(c.luminance(c.readable("#100008")), 0.06)

    def test_a_bright_pick_is_left_alone(self):
        for hx in c.PRESETS:
            self.assertEqual(c.readable(hx), hx)

    def test_the_second_colour_is_a_different_nearby_colour(self):
        for hx in c.PRESETS:
            second = c.secondary(hx)
            self.assertIsNotNone(c.valid_hex(second))
            self.assertNotEqual(second, hx)

    def test_every_preset_is_valid(self):
        self.assertEqual(len(set(c.PRESETS)), len(c.PRESETS))
        self.assertTrue(all(c.valid_hex(p) == p for p in c.PRESETS))
        self.assertEqual(c.DEFAULT, "#d70a53")


class ApplyTests(unittest.TestCase):
    def tearDown(self):
        style.set_feature(c.DEFAULT)
        pixel.set_accent("#d70a53", "#e95420")

    def test_the_feature_colour_reaches_the_style_the_skin_and_home(self):
        used = style.set_feature("#3fe0d0")
        self.assertEqual(used, "#3fe0d0")
        for mode in ("dark", "light"):
            self.assertEqual(style.TOKENS[mode]["accent"], "#3fe0d0")
            self.assertEqual(style.TOKENS[mode]["accent2"], c.secondary("#3fe0d0"))
        sheet = style.stylesheet("dark", None)
        self.assertIn("#3fe0d0", sheet)
        self.assertIn(c.secondary("#3fe0d0"), sheet)                       # the focus outline follows it too
        pm = skin.pixel_map("tab_on", style.TOKENS["dark"])
        self.assertEqual(pm[(7, 0)], "#3fe0d0")                             # the open tab's stripe
        pixel.set_accent("#3fe0d0", style.TOKENS["dark"]["accent2"])
        self.assertEqual(pixel.palette("dark")["accent"], "#3fe0d0")

    def test_a_bad_value_falls_back_to_debian_red(self):
        self.assertEqual(style.set_feature("nonsense"), "#d70a53")
        self.assertEqual(style.set_feature(None), "#d70a53")

    def test_skin_images_live_in_a_folder_per_colour(self):
        import os
        import tempfile
        from unittest import mock

        class FakeImage:
            Format = mock.MagicMock()

            def __init__(self, *a): pass
            def fill(self, *a): pass
            def setPixelColor(self, *a): pass
            def pixelColor(self, *a): return mock.Mock(alpha=lambda: 0)
            def width(self): return 0
            def height(self): return 0
            def save(self, path): open(path, "w").close()
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("sys.modules", {"PyQt6.QtGui": mock.MagicMock(QImage=FakeImage, QColor=lambda x: x)}):
            a = skin.build("dark", dict(style.TOKENS["dark"], accent="#112233"), d)
            b = skin.build("dark", dict(style.TOKENS["dark"], accent="#445566"), d)
        self.assertNotEqual(os.path.dirname(a["card"]), os.path.dirname(b["card"]))
        self.assertIn("112233", a["card"])


if __name__ == "__main__":
    unittest.main()
