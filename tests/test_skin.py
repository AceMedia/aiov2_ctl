import os
import tempfile
import unittest

from ops_gui import skin, style


class ShapeTests(unittest.TestCase):
    def test_corners_are_cut_on_the_diagonal(self):
        s = skin.shape(12, 12, 4, "tl br")
        self.assertNotIn((0, 0), s)
        self.assertNotIn((3, 0), s)                    # x + y < 4
        self.assertIn((4, 0), s)
        self.assertNotIn((11, 11), s)
        self.assertNotIn((8, 11), s)
        self.assertIn((0, 11), s)                      # the other corners stay square
        self.assertIn((11, 0), s)

    def test_each_corner_can_be_cut(self):
        for corner, pixel in (("tl", (0, 0)), ("tr", (11, 0)), ("br", (11, 11)), ("bl", (0, 11))):
            self.assertNotIn(pixel, skin.shape(12, 12, 4, corner), corner)
        self.assertEqual(len(skin.shape(12, 12, 0, "tl")), 144)

    def test_the_border_is_the_outer_two_pixels(self):
        s = skin.shape(20, 20, 6, "tl br")
        edge = skin.border_pixels(s, 20, 20)
        self.assertIn((10, 0), edge)
        self.assertIn((10, 1), edge)
        self.assertNotIn((10, 2), edge)
        self.assertNotIn((10, 10), edge)
        self.assertIn((19, 10), edge)

    def test_the_diagonal_has_a_border_too(self):
        s = skin.shape(20, 20, 6, "tl br")
        edge = skin.border_pixels(s, 20, 20)
        self.assertIn((6, 0), edge)
        self.assertIn((3, 3), edge)                    # on the cut edge
        self.assertNotIn((6, 6), edge)

    def test_an_open_bottom_has_no_bottom_line(self):
        s = skin.shape(20, 20, 6, "tl")
        edge = skin.border_pixels(s, 20, 20, open_bottom=True)
        self.assertNotIn((10, 19), edge)
        self.assertIn((10, 0), edge)
        self.assertIn((0, 10), edge)


class ImageTests(unittest.TestCase):
    def test_every_spec_uses_real_tokens_and_a_slice_that_holds_the_cut(self):
        tokens = style.TOKENS["dark"]
        for name, (slice_px, chamfer, corners, fill, line, extra) in skin.SPECS.items():
            self.assertTrue(fill in tokens or fill.startswith("#"), name)         # a token name or a plain colour
            self.assertTrue(line in tokens or line.startswith("#"), name)
            self.assertGreaterEqual(slice_px, chamfer, name)       # the diagonal fits in one corner slice
            self.assertTrue(set(corners.split()) <= {"tl", "tr", "br", "bl"}, name)

    def test_pixel_maps_are_colours_and_fill_the_middle(self):
        for mode in ("dark", "light"):
            tokens = style.TOKENS[mode]
            for name in skin.SPECS:
                pm = skin.pixel_map(name, tokens)
                size = skin.image_size(skin.SPECS[name][0])
                fill = skin.SPECS[name][3]
                self.assertEqual(pm[(size // 2, size // 2)], tokens.get(fill, fill), name)
                self.assertTrue(all(c.startswith("#") and len(c) == 7 for c in pm.values()), name)
                self.assertNotIn((0, 0), pm) if "tl" in skin.SPECS[name][2] else None

    def test_the_active_tab_has_an_accent_stripe(self):
        tokens = style.TOKENS["dark"]
        pm = skin.pixel_map("tab_on", tokens)
        self.assertEqual(pm[(7, 0)], tokens["accent"])
        self.assertEqual(pm[(7, 1)], tokens["accent"])
        self.assertEqual(pm[(7, 5)], tokens["panel"])
        self.assertNotIn((0, 0), pm)                                   # the top left is cut

    def test_border_image_lines(self):
        line = skin.border_image("card", {"card": "/tmp/x/card.png"})
        self.assertEqual(line, 'border-image: url("/tmp/x/card.png") 8 8 8 8 stretch stretch; border-width: 8px;')


class StylesheetTests(unittest.TestCase):
    PATHS = {n: f"/tmp/skin/{n}.png" for n in skin.SPECS}

    def test_the_sheet_uses_the_skin_when_given_one(self):
        sheet = style.stylesheet("dark", self.PATHS)
        for name in ("card", "button", "tab_on", "field", "pane", "drawer"):
            self.assertIn(f"/tmp/skin/{name}.png", sheet)
        self.assertEqual(style.check(sheet), [])

    def test_it_still_works_without_one(self):
        plain = style.stylesheet("dark")
        self.assertNotIn("border-image", plain)
        self.assertIn("border-radius", plain)

    def test_light_and_dark_both_balance(self):
        for mode in ("dark", "light"):
            self.assertEqual(style.check(style.stylesheet(mode, self.PATHS)), [], mode)

    def test_pixel_font_on_tabs(self):
        sheet = style.stylesheet("dark", self.PATHS)
        self.assertIn('font-family: "Spleen 12x24"', sheet)


if __name__ == "__main__":
    unittest.main()


class PixelTitleTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual([style.pixel_size_for_pt(p) for p in (11, 13, 14, 16, 20, 28, 44)], [16, 16, 24, 24, 32, 32, 64])

    def test_headings_become_pixel_text(self):
        out = style.pixelise_sheet("font-size: 16pt; font-weight: bold;")
        self.assertIn('font-family: "Spleen 12x24"; font-size: 24px', out)
        self.assertNotIn("bold", out)
        self.assertIn("32px", style.pixelise_sheet("font-size: 22pt; font-weight: bold;"))
        self.assertIn("64px", style.pixelise_sheet("font-size: 44pt; font-weight: bold; color: #43a047;"))
        self.assertIn("color: #43a047", style.pixelise_sheet("font-size: 44pt; font-weight: bold; color: #43a047;"))

    def test_body_text_is_left_alone(self):
        for body in ("font-size: 12pt; color: #9e9e9e;", "font-size: 13pt;", "color: red;", ""):
            self.assertEqual(style.pixelise_sheet(body), body)

    def test_it_is_idempotent(self):
        once = style.pixelise_sheet("font-size: 16pt; font-weight: bold;")
        self.assertEqual(style.pixelise_sheet(once), once)

    def test_captions(self):
        self.assertTrue(style.is_caption("font-size: 13pt; color: #9e9e9e;", "Nodes heard"))
        self.assertFalse(style.is_caption("font-size: 13pt; color: #9e9e9e;", "A long sentence that explains what happens next."))
        self.assertFalse(style.is_caption("font-size: 12pt; color: #9e9e9e;", "Nodes"))
        self.assertTrue(style.pixelise_sheet("font-size: 13pt; color: #9e9e9e;", force=True).count("Spleen") == 1)


class ParityTests(unittest.TestCase):
    def test_tabs_have_a_cut_top_left_and_a_flared_foot(self):
        for name in ("tab_off", "tab_hover", "tab_on"):
            self.assertEqual(skin.SPECS[name][2], "tl")
            art = skin.SPECS[name][5]["art"]
            self.assertEqual(len(art), skin.image_size(skin.SPECS[name][0]))
            self.assertTrue(all(len(row) == len(art) for row in art), name)
            self.assertTrue(art[0].startswith("....."))                # the cut corner
            self.assertTrue(art[-1].endswith("##"))                    # the foot reaches the box edge at the bottom
            self.assertTrue(art[3].endswith("...."))                   # but the body stops short of it higher up
            self.assertTrue(art[-1].endswith("#" * 2))
            self.assertNotIn("#" * 6, art[-1])                         # no bottom edge: the tab joins the pane
        self.assertTrue(skin.SPECS["tab_on"][5].get("open_bottom"))

    def test_the_foot_widens_a_step_each_row(self):
        art = skin.TAB_OFF
        right = [max(i for i, c in enumerate(row) if c != ".") for row in art[-6:]]
        self.assertEqual(right, list(range(18, 24)))                    # an eight pixel foot, a pixel wider every row
        self.assertEqual(max(i for i, c in enumerate(art[5]) if c != "."), 16)
        # the stretched middle columns (8 to 11) must be one colour on every row, or the tab fades across its width
        for row in art[2:]:
            self.assertEqual(len(set(row[10:14])), 1, row)

    def test_a_plain_colour_works_as_a_fill(self):
        pm = skin.pixel_map("chip_off", style.TOKENS["dark"])
        self.assertEqual(pm[(10, 10)], "#616161")

    def test_no_rounded_corners_are_left_in_the_skinned_sheet(self):
        sheet = style.stylesheet("dark", {n: f"/tmp/{n}.png" for n in skin.SPECS})
        self.assertNotIn("border-radius: 8px", sheet)
        self.assertNotIn("border-radius: 12px", sheet)
        self.assertNotIn("border-radius: 10px", sheet)
        self.assertNotIn("border-radius: 6px", sheet)
        self.assertNotIn("border-radius: 5px", sheet)

    def test_the_slash_divider(self):
        from ops_gui import statusbar
        out = statusbar.chips_html([("a", "good"), ("b", "good")])
        self.assertIn('>/</span>', out)
        self.assertNotIn("|", out)
