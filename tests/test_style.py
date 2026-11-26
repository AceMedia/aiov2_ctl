"""The shared stylesheet must be well formed in both themes."""
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ops_gui import style  # noqa: E402


class StyleTests(unittest.TestCase):
    def test_both_themes_are_well_formed(self):
        for mode in ("light", "dark"):
            self.assertEqual(style.check(style.stylesheet(mode)), [], mode)

    def test_every_token_is_a_hex_colour_in_both_themes(self):
        self.assertEqual(set(style.TOKENS["dark"]), set(style.TOKENS["light"]))
        for mode in style.TOKENS.values():
            for value in mode.values():
                self.assertRegex(value, r"^#[0-9a-f]{6}$")

    def test_inactive_tabs_sit_lower_than_the_selected_one(self):
        sheet = style.stylesheet("dark")
        self.assertIn("margin-top: 9px", sheet)
        self.assertIn("margin-top: 0px", sheet)

    def test_modern_font_first_with_a_safe_fallback(self):
        self.assertEqual(style.FONT_FAMILIES[0], "Inter")
        self.assertIn("DejaVu Sans", style.FONT_FAMILIES)


def _lum(hex_colour):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class LightThemeReadabilityTests(unittest.TestCase):
    """Helper text and the green of "on" must be readable on each theme's cards."""

    def test_helper_text_grey_is_swapped_for_the_theme_and_back(self):
        dark = "font-size: 11pt; color: #9e9e9e;"
        light = style.muted_sheet(dark, "light")
        self.assertIn(style.MUTED["light"], light)
        self.assertNotIn("#9e9e9e", light)
        self.assertEqual(style.muted_sheet(light, "dark"), dark)
        self.assertEqual(style.muted_sheet(dark, "dark"), dark)
        self.assertEqual(style.muted_sheet("font-size: 13pt;", "light"), "font-size: 13pt;")
        self.assertEqual(style.muted_sheet(None, "light"), "")

    def test_helper_text_can_be_read_on_both_themes(self):
        for mode in ("dark", "light"):
            self.assertGreaterEqual(_contrast(style.MUTED[mode], style.TOKENS[mode]["card"]), 4.5, mode)

    def test_the_green_of_on_can_be_read_on_both_themes(self):
        for mode in ("dark", "light"):
            self.assertGreaterEqual(_contrast(style.good_colour(mode), style.TOKENS[mode]["card"]), 3.0, mode)

    def test_a_caption_is_still_a_caption_after_the_swap(self):
        sheet = style.muted_sheet("font-size: 13pt; color: #9e9e9e;", "light")
        self.assertTrue(style.is_caption(sheet, "Nodes heard"))

    def test_a_picked_table_row_uses_the_feature_colour(self):
        for mode in ("dark", "light"):
            self.assertIn("QTableWidget::item:selected", style.stylesheet(mode))


if __name__ == "__main__":
    unittest.main()
