import unittest

from ops_gui import skin, style


class ParityTests(unittest.TestCase):
    TOKENS = style.TOKENS["dark"]

    def test_cards_and_rows_have_the_home_tick(self):
        for name in ("card", "row"):
            pm = skin.pixel_map(name, self.TOKENS)
            size = skin.image_size(skin.SPECS[name][0])
            ticks = [p for p, c in pm.items() if c == self.TOKENS["accent2"]]
            self.assertEqual(len(ticks), 10)
            self.assertTrue(all(y < 2 and x >= size - 8 for x, y in ticks))        # on the top edge, in the right slice

    def test_tiles_do_not(self):
        pm = skin.pixel_map("tile", self.TOKENS)
        self.assertNotIn(self.TOKENS["accent2"], pm.values())

    def test_the_stylesheet_styles_section_tags_and_headers(self):
        for skinned in (None, {n: f"/tmp/{n}.png" for n in skin.SPECS}):
            sheet = style.stylesheet("dark", skinned)
            self.assertIn("#tag {", sheet)
            self.assertIn(self.TOKENS["accent"], sheet.split("#tag {")[1].split("}")[0])
            self.assertIn(f"QHeaderView::section {{ color: {self.TOKENS['accent2']}; }}", sheet)

    def test_card_titles_are_tags(self):
        import inspect
        from ops_gui import dashboard, flipper_panel, settings_ui
        for mod in (dashboard, flipper_panel, settings_ui):
            self.assertIn('setObjectName("tag")', inspect.getsource(mod), mod.__name__)
            self.assertIn("title.upper()", inspect.getsource(mod), mod.__name__)


if __name__ == "__main__":
    unittest.main()
