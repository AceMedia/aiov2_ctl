"""The shared pieces IGA's row reuses: the pixel-icon renderer's checks, glass in high contrast, IGA's tab style,
and readable selected text on any feature colour."""
import unittest

from ops_gui import foldertabs, pixelicon, skin, style


class PixelIconTests(unittest.TestCase):
    def test_bounds_and_lone_pixels(self):
        rows = ["....", ".##.", ".##.", "...#"]
        self.assertEqual(pixelicon.ink_bounds(rows), (1, 1, 3, 3))
        self.assertEqual(pixelicon.lone_pixels(rows), [(3, 3)])
        self.assertIsNone(pixelicon.ink_bounds(["...."]))


class GlassTests(unittest.TestCase):
    def test_solid_in_high_contrast_glass_otherwise(self):
        self.assertEqual(foldertabs.glass_opacity(True), 1.0)
        self.assertEqual(foldertabs.glass_opacity(False), skin.GLASS_ALPHA / 255)


class StyleTests(unittest.TestCase):
    def test_igas_compact_tabs_overlap_by_four_and_its_field_has_no_frame(self):
        for mode in ("dark", "light"):
            sheet = style.stylesheet(mode)
            self.assertIn("#igatabs QTabBar::tab {", sheet)
            self.assertIn("margin: 4px -4px 0px 0px;", sheet)          # not open: four pixels lower
            self.assertIn("margin: 0px -4px -2px 0px;", sheet)         # open: joins the pane
            self.assertIn("QLineEdit#tabfield", sheet)
            self.assertIn(f"background: {style.TOKENS[mode]['field']}", sheet)

    def test_selected_text_reads_on_any_feature_colour(self):
        self.assertEqual(style.readable_text("#ffff00"), "#000000")
        self.assertEqual(style.readable_text("#000080"), "#ffffff")
        self.assertEqual(style.readable_text("#d70a53"), "#ffffff")
        self.assertEqual(style.readable_text("nonsense"), "#ffffff")


if __name__ == "__main__":
    unittest.main()
