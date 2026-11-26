"""Every tab has its icon everywhere it is shown: the PNG on the tab bar (both sizes) and its own bitmap on Help."""
import os
import struct
import unittest

from ops_gui import data, helpview

ICONS = os.path.join(os.path.dirname(os.path.abspath(data.__file__)), "icons")
SIZES = (32, 64)
ALLOWED = set(".#o")                     # tab bitmaps have no parts that light up, so no variant letters


def every_tab():
    """Every name tab_names can return: all the optional tabs shown and all the antenna rails on."""
    rails = {rail: "on" for _, rail in data.ANTENNA_RAILS}
    return data.tab_names({"rails": rails}, True, marauder_shown=True, home=True, network=True, bluetooth=True,
                          everything=True)


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


class IconParityTests(unittest.TestCase):
    def test_all_the_optional_tabs_are_in_the_list_being_checked(self):
        names = every_tab()
        self.assertEqual(len(names), len(set(names)))
        for name in ["Home", "Everything", "Bluetooth", "Network", "Marauder", "Flipper"] + [t for t, _ in data.ANTENNA_RAILS]:
            self.assertIn(name, names)

    def test_every_tab_has_a_tab_bar_icon_entry(self):
        for name in every_tab():
            self.assertIn(name, data.TAB_ICONS, name)

    def test_every_tab_has_its_own_help_bitmap(self):
        seen = {}
        for name in every_tab():
            self.assertIn(name, helpview.TAB_ICONS, f"{name} would show the Overview icon on Help")
            art = "".join(helpview.TAB_ICONS[name])
            self.assertNotIn(art, seen, f"{name} has the same bitmap as {seen.get(art)}")
            seen[art] = name

    def test_no_icon_entry_is_left_over_for_a_tab_that_cannot_exist(self):
        names = set(every_tab())
        self.assertEqual(set(data.TAB_ICONS) - names, set())
        self.assertEqual(set(helpview.TAB_ICONS) - names, set())

    def test_every_help_bitmap_is_sixteen_rows_of_sixteen_allowed_characters(self):
        for name, rows in helpview.TAB_ICONS.items():
            self.assertEqual(len(rows), 16, name)
            for row in rows:
                self.assertEqual(len(row), 16, f"{name}: {row!r}")
                self.assertTrue(set(row) <= ALLOWED, f"{name}: {row!r}")
            self.assertIn("#", "".join(rows), name)

    def test_every_referenced_png_is_there_at_both_sizes(self):
        stems = {stem: f"tab {tab}" for tab, stem in data.TAB_ICONS.items()}
        stems.update({info[1]: f"rail {rail}" for rail, info in data.RAIL_INFO.items()})
        for stem, user in sorted(stems.items()):
            for size in SIZES:
                path = os.path.join(ICONS, str(size), f"{stem}-bare.png")
                self.assertTrue(os.path.isfile(path), f"{user}: {stem}-bare.png is missing at {size} px")
                self.assertEqual(png_size(path), (size, size), f"{user}: {path}")

    def test_the_two_size_folders_hold_the_same_files(self):
        self.assertEqual(sorted(os.listdir(os.path.join(ICONS, "32"))), sorted(os.listdir(os.path.join(ICONS, "64"))))

    def test_the_path_helpers_find_every_icon(self):
        for size in SIZES:
            for name in every_tab():
                self.assertTrue(data.tab_icon_path(name, size), f"{name} {size}")
            for rail in data.RAIL_INFO:
                self.assertTrue(data.rail_icon_path(rail, size), f"{rail} {size}")


if __name__ == "__main__":
    unittest.main()
