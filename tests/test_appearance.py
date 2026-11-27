"""High contrast, the plain backgrounds and a chosen font (ops_gui/style.py). No screen is needed."""
import re
import unittest

from ops_gui import style


def luminance(hex_colour):
    parts = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    parts = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class Base(unittest.TestCase):
    def tearDown(self):
        style.set_contrast(False)
        style.set_font("")


class ContrastTests(Base):
    def test_high_contrast_is_black_and_white_with_solid_edges(self):
        self.assertTrue(style.set_contrast(True))
        dark, light = style.TOKENS["dark"], style.TOKENS["light"]
        self.assertEqual((dark["bg"], dark["card"], dark["text"], dark["border"]), ("#000000", "#000000", "#ffffff", "#ffffff"))
        self.assertEqual((light["bg"], light["card"], light["text"], light["border"]), ("#ffffff", "#ffffff", "#000000", "#000000"))

    def test_text_and_helper_text_are_far_clearer_than_the_usual_minimum(self):
        style.set_contrast(True)
        for mode in ("dark", "light"):
            t = style.TOKENS[mode]
            for surface in ("bg", "panel", "card", "row", "btn", "field", "tab_off"):
                self.assertGreaterEqual(contrast(t["text"], t[surface]), 15, (mode, surface))
                self.assertGreaterEqual(contrast(t["muted"], t[surface]), 10, (mode, surface))
                self.assertGreaterEqual(contrast(t["border"], t[surface]), 15, (mode, surface))

    def test_the_feature_colour_is_left_as_the_person_set_it(self):
        before = style.TOKENS["dark"]["accent"], style.TOKENS["dark"]["accent2"]
        style.set_contrast(True)
        self.assertEqual((style.TOKENS["dark"]["accent"], style.TOKENS["dark"]["accent2"]), before)

    def test_switching_it_off_puts_every_colour_back(self):
        before = {mode: dict(values) for mode, values in style.TOKENS.items()}
        style.set_contrast(True)
        self.assertFalse(style.set_contrast(False))
        self.assertEqual(style.TOKENS, before)

    def test_the_style_sheet_is_still_sound_in_high_contrast(self):
        style.set_contrast(True)
        for mode in ("dark", "light"):
            sheet = style.stylesheet(mode)
            self.assertEqual(style.check(sheet), [])
            self.assertIn("#setting", sheet)


class BackgroundTests(Base):
    def test_the_gradient_is_the_usual_background_and_anything_unknown_means_it(self):
        self.assertIsNone(style.background_fill("gradient", "dark"))
        self.assertIsNone(style.background_fill(None, "dark"))
        self.assertIsNone(style.background_fill("sparkles", "light"))
        self.assertEqual(style.background(True), "gradient")

    def test_plain_is_the_themes_own_flat_colour(self):
        self.assertEqual(style.background_fill("plain", "dark"), style.TOKENS["dark"]["bg"])
        self.assertEqual(style.background_fill("plain", "light"), style.TOKENS["light"]["bg"])

    def test_black_is_black_in_either_theme(self):
        self.assertEqual(style.background_fill("black", "dark"), "#000000")
        self.assertEqual(style.background_fill("black", "light"), "#000000")

    def test_high_contrast_has_no_gradient_whatever_is_chosen(self):
        style.set_contrast(True)
        for choice in ("gradient", "plain", None):
            self.assertEqual(style.background_fill(choice, "dark"), "#000000", choice)
            self.assertEqual(style.background_fill(choice, "light"), "#ffffff", choice)
        self.assertEqual(style.background_fill("black", "light"), "#000000")


class FontTests(Base):
    def test_with_no_font_chosen_the_apps_own_are_used(self):
        self.assertEqual(style.families(), style.FONT_FAMILIES)
        self.assertIn('"Spleen 8x16"', style.stylesheet("dark"))

    def test_a_chosen_font_comes_first_and_replaces_the_pixel_font_in_the_style_sheet(self):
        self.assertEqual(style.set_font("DejaVu Serif"), "DejaVu Serif")
        self.assertEqual(style.families()[0], "DejaVu Serif")
        self.assertEqual(style.families()[1:], style.FONT_FAMILIES)
        sheet = style.stylesheet("dark")
        self.assertNotIn("Spleen", sheet)
        self.assertIn('font-family: "DejaVu Serif"; font-size: 16px;', sheet)
        self.assertEqual(style.check(sheet), [])

    def test_headings_turned_into_the_pixel_font_get_the_chosen_one_instead(self):
        style.set_font("DejaVu Serif")
        out = style.pixelise_sheet("font-size: 16pt; font-weight: bold;")
        self.assertIn('"DejaVu Serif"', out)
        self.assertNotIn("Spleen", out)

    def test_an_empty_name_goes_back_to_the_apps_own(self):
        style.set_font("DejaVu Serif")
        self.assertIsNone(style.set_font("  "))
        self.assertIn("Spleen", style.stylesheet("dark"))
        self.assertIsNone(style.set_font(None))

    def test_a_name_cannot_break_out_of_the_style_sheet(self):
        self.assertEqual(style.set_font('Evil"; color: red; x: "'), "Evil color: red x:")
        self.assertEqual(style.check(style.stylesheet("dark")), [])


class SubTabTests(Base):
    def test_sub_tabs_have_room_either_side_of_their_words(self):
        for line in re.findall(r"#subtabs QTabBar::tab[^{]*\{[^}]*\}", style.stylesheet("dark")):
            top, right, bottom, left = re.search(r"padding: (\d+)px (\d+)px (\d+)px (\d+)px", line).groups()
            self.assertGreaterEqual(int(left), 10, line)
            self.assertGreaterEqual(int(right), 10, line)


if __name__ == "__main__":
    unittest.main()
