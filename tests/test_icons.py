"""The project's own launcher icons (tools/icons.py, img/) and the Icon lines that point at them."""
import importlib.util
import os
import struct
import unittest

from ops_gui import launchers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("icons_tool", os.path.join(REPO, "tools", "icons.py"))
icons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(icons)


class DrawingTests(unittest.TestCase):
    def test_every_shape_of_every_icon_is_on_the_grid_and_a_colour(self):
        for kind in icons.NAMES:
            shapes = icons.shapes(kind)
            self.assertGreater(len(shapes), 8, kind)
            for left, top, right, bottom, colour in shapes:
                self.assertTrue(0 <= left < right <= icons.GRID and 0 <= top < bottom <= icons.GRID, (kind, left, top, right, bottom))
                self.assertRegex(colour, r"^#[0-9a-f]{6}$")

    def test_the_corners_of_the_plate_are_left_clear(self):
        covered = set()
        for left, top, right, bottom, _ in icons.tile():
            covered |= {(x, y) for x in range(left, right) for y in range(top, bottom)}
        for corner in ((0, 0), (31, 0), (0, 31), (31, 31)):
            self.assertNotIn(corner, covered)
        self.assertIn((16, 0), covered)
        self.assertIn((0, 16), covered)

    def test_an_icon_it_does_not_know_is_an_error(self):
        with self.assertRaises(ValueError):
            icons.shapes("nonsense")

    def test_all_four_rail_colours_are_on_every_icon(self):
        for kind in icons.NAMES:
            colours = {s[4] for s in icons.shapes(kind)}
            self.assertTrue(set(icons.RAILS) <= colours, kind)


class FileTests(unittest.TestCase):
    def test_each_icon_is_in_the_repository_as_a_128_pixel_png(self):
        for name in icons.NAMES.values():
            with open(os.path.join(REPO, "img", name), "rb") as f:
                head = f.read(24)
            self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n", name)
            self.assertEqual(struct.unpack(">II", head[16:24]), (128, 128), name)

    def test_the_files_are_what_the_tool_draws(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        for kind, name in icons.NAMES.items():
            saved = Image.open(os.path.join(REPO, "img", name)).convert("RGBA")
            self.assertEqual(saved.tobytes(), icons.draw(kind).tobytes(), name)


class IconLineTests(unittest.TestCase):
    def test_the_projects_own_icon_when_the_checkout_has_it_else_the_themes(self):
        self.assertEqual(launchers.icon_for("/r", "ops-app.png", "fallback", isfile=lambda p: True), "/r/img/ops-app.png")
        self.assertEqual(launchers.icon_for("/r", "ops-app.png", "fallback", isfile=lambda p: False), "fallback")

    def test_the_launchers_point_at_them(self):
        made = launchers.entries(REPO)
        self.assertIn(f"Icon={os.path.join(REPO, 'img', 'ops-app.png')}\n", made[launchers.GUI_FILE])
        self.assertIn(f"Icon={os.path.join(REPO, 'img', 'ops-console.png')}\n", made[launchers.CLI_FILE])
        bare = launchers.entries(REPO, isfile=lambda p: False)
        self.assertIn("Icon=utilities-system-monitor\n", bare[launchers.GUI_FILE])
        self.assertIn("Icon=utilities-terminal\n", bare[launchers.CLI_FILE])


if __name__ == "__main__":
    unittest.main()
