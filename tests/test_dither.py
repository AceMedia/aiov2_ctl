import unittest

from ops_gui import dither as d, skin


class DitherTests(unittest.TestCase):
    def test_strongest_at_the_top_fading_towards_the_bottom(self):
        rows = d.levels(720)
        self.assertEqual(rows[-1], (718, []))                           # nothing left by the very bottom
        counts = [len(xs) for _y, xs in rows]
        self.assertGreater(sum(counts[:60]), sum(counts[-60:]))         # the top has more of it than the bottom
        self.assertGreater(sum(counts[:60]), 0)

    def test_rows_step_by_the_cell_size_and_cover_the_height(self):
        rows = d.levels(100)
        self.assertEqual([y for y, _ in rows][:3], [0, 2, 4])
        self.assertEqual(rows[-1][0], 98)

    def test_it_is_ordered_not_random(self):
        self.assertEqual(d.levels(300), d.levels(300))

    def test_tiny_heights_do_not_crash(self):
        for h in (0, 1, 2, 3):
            self.assertTrue(d.levels(h))


class BevelTests(unittest.TestCase):
    TOKENS = {"btn": "#262b32", "border": "#2f343c", "accent": "#d70a53", "tab_hover": "#262b32"}

    def test_raised_buttons_are_lit_top_left_and_shaded_bottom_right(self):
        pm = skin.pixel_map("button", self.TOKENS)
        size = skin.image_size(skin.SPECS["button"][0])
        lit, shade = pm[(2, 8)], pm[(size - 3, 8)]
        self.assertNotEqual(lit, shade)
        self.assertGreater(int(lit[1:3], 16), int(shade[1:3], 16))

    def test_a_pressed_button_swaps_them(self):
        up = skin.pixel_map("button", self.TOKENS)
        down = skin.pixel_map("button_press", dict(self.TOKENS, accent="#262b32"))
        size = skin.image_size(skin.SPECS["button"][0])
        self.assertLess(int(down[(2, 8)][1:3], 16), int(down[(size - 3, 8)][1:3], 16))
        self.assertNotEqual(up[(2, 8)], down[(2, 8)])

    def test_mix(self):
        self.assertEqual(skin.mix("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(skin.mix("#102030", "#102030", 0.7), "#102030")

    def test_the_charging_chip_is_debian_orange_not_blue(self):
        self.assertEqual(skin.SPECS["chip_on"][3], "#e95420")


if __name__ == "__main__":
    unittest.main()
