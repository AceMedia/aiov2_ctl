"""The loading screen (ops_gui/splash.py). No screen is needed."""
import unittest

from ops_gui import splash, style


class GlyphTests(unittest.TestCase):
    def test_every_letter_of_the_name_has_a_glyph_five_by_seven(self):
        for ch in splash.TITLE:
            self.assertIn(ch, splash.GLYPHS, ch)
        for ch, rows in splash.GLYPHS.items():
            self.assertEqual(len(rows), splash.ROWS, ch)
            self.assertTrue(all(len(row) == 5 and set(row) <= {"X", "."} for row in rows), ch)

    def test_the_lit_squares_of_a_letter_and_of_two(self):
        self.assertEqual(len(splash.cells("I")), 5 + 5 + 5)
        self.assertEqual(splash.cells(" "), [])
        self.assertEqual(splash.cells("?"), [], "a letter it has no glyph for is a gap, not an error")
        two = splash.cells("II")
        self.assertEqual(len(two), 30)
        self.assertEqual(max(c for c, _ in two), splash.ADVANCE + 4)
        self.assertEqual(splash.columns("II"), 11)
        self.assertEqual(splash.columns(""), 0)


class LayoutTests(unittest.TestCase):
    def test_everything_is_on_the_screen_and_centred_at_every_size(self):
        for width, height in ((1280, 720), (1280, 696), (720, 640), (480, 320), (200, 100)):
            lay = splash.layout(width, height)
            size, (x, y) = lay["size"], lay["title"]
            self.assertGreaterEqual(size, 1)
            title_w = splash.columns(splash.TITLE) * size
            self.assertGreaterEqual(x, 0, (width, height))
            self.assertLessEqual(x + title_w, width, (width, height))
            self.assertAlmostEqual(x + title_w / 2, width / 2, delta=1)
            self.assertEqual(len(lay["strip"]), splash.STRIP)
            for bx, by, bw, bh in lay["strip"]:
                self.assertGreaterEqual(bx, 0, (width, height))
                self.assertLessEqual(bx + bw, width, (width, height))
                self.assertLessEqual(by + bh, height, (width, height))
                self.assertGreater(by, y + splash.ROWS * size - 1, "the strip is under the name")

    def test_on_the_uconsole_the_name_is_big(self):
        self.assertGreaterEqual(splash.layout(1280, 720)["size"], 8)


class ColourTests(unittest.TestCase):
    def test_it_follows_the_saved_theme_and_the_system_when_none_is_saved(self):
        t = style.TOKENS
        self.assertEqual(splash.colours({"theme": "light"}, True, t)[0], t["light"]["bg"])
        self.assertEqual(splash.colours({"theme": "dark"}, False, t)[0], t["dark"]["bg"])
        self.assertEqual(splash.colours({}, True, t)[0], t["dark"]["bg"])
        self.assertEqual(splash.colours({}, False, t)[0], t["light"]["bg"])

    def test_high_contrast_is_pure_black_or_white(self):
        t = style.TOKENS
        self.assertEqual(splash.colours({"theme": "dark", "high_contrast": True}, True, t)[:2], ("#000000", "#ffffff"))
        self.assertEqual(splash.colours({"theme": "light", "high_contrast": True}, True, t)[:2], ("#ffffff", "#000000"))

    def test_the_feature_colour_is_the_saved_one_when_it_is_a_colour(self):
        t = style.TOKENS
        self.assertEqual(splash.colours({"theme": "dark", "accent": "#4c8dff"}, True, t)[2], "#4c8dff")
        for bad in (None, "", "blue", "#12345", 7, "#gggggg"):
            self.assertEqual(splash.colours({"theme": "dark", "accent": bad}, True, t)[2], t["dark"]["accent"], bad)

    def test_the_strip_fades_from_the_feature_colour_towards_the_background(self):
        strip = splash.fade("#000000", "#4c8dff")
        self.assertEqual(len(strip), splash.STRIP)
        self.assertEqual(strip[0], "#4c8dff")
        blues = [int(c[5:7], 16) for c in strip]
        self.assertEqual(blues, sorted(blues, reverse=True))
        self.assertNotEqual(strip[-1], "#000000", "the last block can still be seen")


class WantedTests(unittest.TestCase):
    def test_it_shows_unless_asked_not_to(self):
        self.assertTrue(splash.wanted(["ops_gui"]))
        self.assertTrue(splash.wanted(["ops_gui", "--fullscreen"]))
        self.assertFalse(splash.wanted(["ops_gui", "--no-splash"]))


if __name__ == "__main__":
    unittest.main()
