"""The Apps tab's plain parts (ops_gui/apps.py): finding programs in the menu, sorting them into its sections,
moving round a grid, and what is run to start one."""
import unittest

from ops_gui import apps, data

TETRIS = """[Desktop Entry]
Name=Tetris CLI
Name[de]=Tetris im Terminal
Comment=Ascii/cli rendition of tetris.
Exec=/usr/games/tetris/tetris
Icon=tetris
Terminal=true
Type=Application
Categories=Game;BlocksGame;
"""
STEAMLINK = """[Desktop Entry]
Name=Steam Link
Exec=/usr/bin/steamlink %u
Icon=steamlink
Terminal=false
Type=Application
Categories=Game;
"""
SNES = """[Desktop Entry]
Name=Snes9x
Comment=A Super Nintendo emulator
Type=Application
Categories=Game;Emulator;
Exec=/usr/bin/flatpak run --branch=stable --arch=aarch64 --command=snes9x-gtk --file-forwarding com.snes9x.Snes9x @@ %F @@
Icon=com.snes9x.Snes9x
"""
EDITOR = """[Desktop Entry]
Name=Text Editor
Exec=editor %F
Type=Application
Categories=Utility;TextEditor;
"""
BROWSER = """[Desktop Entry]
Name=Web Browser
Exec=browser %U
Type=Application
Categories=Network;WebBrowser;
"""
PLAYER = """[Desktop Entry]
Name=Player
Exec=player
Type=Application
Categories=Audio;Video;
"""
ODD = """[Desktop Entry]
Name=Odd One
Exec=odd
Type=Application
"""


def tree(files):
    def listdir(folder):
        names = [p[len(folder) + 1:] for p in files if p.startswith(folder + "/") and "/" not in p[len(folder) + 1:]]
        if not names:
            raise FileNotFoundError(folder)
        return names
    return listdir, files.get


def found(files, gone=(), session=()):
    listdir, read = tree(files)
    dirs = ["/home/u/.local/share", "/usr/share", "/var/lib/flatpak/exports/share"]
    return apps.find_apps(dirs, listdir, read, lambda program: None if program in gone else program, session)


def app(name, *categories):
    return {"id": name.lower(), "name": name, "categories": list(categories)}


class EntryTests(unittest.TestCase):
    def test_only_the_main_part_is_read_and_translations_are_left(self):
        entry = apps.parse_entry("# note\n[Other]\nName=No\n" + TETRIS + "[Desktop Action x]\nName=Also no\n")
        self.assertEqual(entry["Name"], "Tetris CLI")
        self.assertEqual(entry["Exec"], "/usr/games/tetris/tetris")
        self.assertNotIn("Name[de]", entry)

    def test_any_program_with_a_name_and_something_to_run_is_shown(self):
        for text in (TETRIS, SNES, EDITOR, ODD):
            self.assertTrue(apps.is_shown(apps.parse_entry(text)))

    def test_hidden_entries_links_and_ones_with_nothing_to_run_are_not_shown(self):
        base = apps.parse_entry(TETRIS)
        self.assertFalse(apps.is_shown(dict(base, NoDisplay="true")))
        self.assertFalse(apps.is_shown(dict(base, Hidden="True")))
        self.assertFalse(apps.is_shown(dict(base, Type="Link")))
        self.assertFalse(apps.is_shown({k: v for k, v in base.items() if k != "Exec"}))
        self.assertFalse(apps.is_shown({k: v for k, v in base.items() if k != "Name"}))

    def test_an_entry_kept_for_another_desktop_is_not_shown(self):
        base = apps.parse_entry(EDITOR)
        self.assertFalse(apps.is_shown(dict(base, OnlyShowIn="KDE;"), ["LXQt"]))
        self.assertFalse(apps.is_shown(dict(base, OnlyShowIn="KDE;"), []), "no desktop known: not this one's either")
        self.assertTrue(apps.is_shown(dict(base, OnlyShowIn="KDE;LXQt;"), ["LXQt"]))
        self.assertFalse(apps.is_shown(dict(base, NotShowIn="LXQt;"), ["LXQt"]))
        self.assertTrue(apps.is_shown(dict(base, NotShowIn="KDE;"), ["LXQt"]))

    def test_the_sessions_desktop_names(self):
        self.assertEqual(apps.desktops({"XDG_CURRENT_DESKTOP": "labwc:wlroots"}), ["labwc", "wlroots"])
        self.assertEqual(apps.desktops({}), [])


class CommandTests(unittest.TestCase):
    def test_a_plain_command_is_kept(self):
        self.assertEqual(apps.command("/usr/games/tetris/tetris"), ["/usr/games/tetris/tetris"])

    def test_the_place_for_a_file_is_taken_out(self):
        self.assertEqual(apps.command("/usr/bin/steamlink %u"), ["/usr/bin/steamlink"])

    def test_flatpaks_wrapping_goes_with_it(self):
        self.assertEqual(apps.command(apps.parse_entry(SNES)["Exec"]),
                         ["/usr/bin/flatpak", "run", "--branch=stable", "--arch=aarch64", "--command=snes9x-gtk",
                          "--file-forwarding", "com.snes9x.Snes9x"])

    def test_quotes_a_real_percent_sign_and_an_empty_argument_survive(self):
        self.assertEqual(apps.command('game "two words" --rate=50%% "" %i'), ["game", "two words", "--rate=50%", ""])

    def test_a_line_that_cannot_be_read_gives_nothing(self):
        self.assertEqual(apps.command('game "unfinished'), [])
        self.assertEqual(apps.command(""), [])
        self.assertEqual(apps.command(None), [])


class FindingTests(unittest.TestCase):
    def test_programs_are_found_in_every_folder_and_listed_by_name(self):
        got = found({"/usr/share/applications/tetris.desktop": TETRIS,
                     "/usr/share/applications/steamlink.desktop": STEAMLINK,
                     "/usr/share/applications/editor.desktop": EDITOR,
                     "/usr/share/applications/notes.txt": TETRIS,
                     "/var/lib/flatpak/exports/share/applications/com.snes9x.Snes9x.desktop": SNES})
        self.assertEqual([a["name"] for a in got], ["Snes9x", "Steam Link", "Tetris CLI", "Text Editor"])
        tetris = got[2]
        self.assertEqual((tetris["id"], tetris["terminal"], tetris["icon"], tetris["about"], tetris["categories"]),
                         ("tetris", True, "tetris", "Ascii/cli rendition of tetris.", ["Game", "BlocksGame"]))
        self.assertFalse(got[1]["terminal"])

    def test_a_persons_own_entry_wins_over_the_systems_and_can_hide_it(self):
        mine = TETRIS.replace("Name=Tetris CLI", "Name=My Tetris")
        got = found({"/home/u/.local/share/applications/tetris.desktop": mine,
                     "/usr/share/applications/tetris.desktop": TETRIS})
        self.assertEqual([a["name"] for a in got], ["My Tetris"])
        got = found({"/home/u/.local/share/applications/tetris.desktop": TETRIS + "Hidden=true\n",
                     "/usr/share/applications/tetris.desktop": TETRIS})
        self.assertEqual(got, [])

    def test_a_program_that_has_gone_is_not_listed(self):
        files = {"/usr/share/applications/tetris.desktop": TETRIS,
                 "/usr/share/applications/steamlink.desktop": STEAMLINK + "TryExec=steamlink-check\n"}
        self.assertEqual([a["name"] for a in found(files, gone=("/usr/games/tetris/tetris",))], ["Steam Link"])
        self.assertEqual([a["name"] for a in found(files, gone=("steamlink-check",))], ["Tetris CLI"])

    def test_the_sessions_desktop_decides_entries_kept_for_one(self):
        files = {"/usr/share/applications/editor.desktop": EDITOR + "OnlyShowIn=KDE;\n"}
        self.assertEqual(found(files, session=["LXQt"]), [])
        self.assertEqual(len(found(files, session=["KDE"])), 1)

    def test_no_folders_at_all_is_no_programs_not_an_error(self):
        self.assertEqual(found({}), [])

    def test_a_file_that_cannot_be_read_is_passed_over(self):
        def listdir(folder):
            if folder == "/usr/share/applications":
                return ["a.desktop"]
            raise OSError()
        self.assertEqual(apps.find_apps(["/usr/share"], listdir, lambda path: None, lambda p: p), [])


class SectionTests(unittest.TestCase):
    def test_games_come_first_and_empty_sections_are_left_out(self):
        got = apps.sections([app("Browser", "Network"), app("Editor", "Utility", "TextEditor"), app("Tetris", "Game")])
        self.assertEqual([(title, [a["name"] for a in members]) for title, members in got],
                         [("Games", ["Tetris"]), ("Internet", ["Browser"]), ("Accessories", ["Editor"])])

    def test_a_program_is_in_every_section_its_categories_name_but_once_in_each(self):
        got = dict(apps.sections([app("Player", "Audio", "Video", "AudioVideo"), app("Tool", "System", "Settings")]))
        self.assertEqual([a["name"] for a in got["Sound & Video"]], ["Player"])
        self.assertEqual([a["name"] for a in got["System"]], ["Tool"])
        self.assertEqual([a["name"] for a in got["Settings"]], ["Tool"])

    def test_a_program_with_no_known_category_goes_in_other_at_the_end(self):
        got = apps.sections([app("Odd"), app("Niche", "X-Made-Up"), app("Tetris", "Game")])
        self.assertEqual([title for title, _ in got], ["Games", "Other"])
        self.assertEqual([a["name"] for a in got[1][1]], ["Odd", "Niche"])

    def test_no_programs_is_no_sections(self):
        self.assertEqual(apps.sections([]), [])

    def test_real_entries_land_where_the_menu_puts_them(self):
        got = dict(apps.sections(found({"/usr/share/applications/a.desktop": TETRIS, "/usr/share/applications/b.desktop": BROWSER,
                                        "/usr/share/applications/c.desktop": PLAYER, "/usr/share/applications/d.desktop": ODD})))
        self.assertEqual({title: [a["name"] for a in members] for title, members in got.items()},
                         {"Games": ["Tetris CLI"], "Internet": ["Web Browser"], "Sound & Video": ["Player"], "Other": ["Odd One"]})


class GridTests(unittest.TestCase):
    """Ten tiles in rows of four:  0 1 2 3 / 4 5 6 7 / 8 9"""

    def step(self, index, direction, count=10, columns=4):
        return apps.grid_step(index, count, columns, direction)

    def test_left_and_right_move_along_a_row_and_stop_at_its_ends(self):
        self.assertEqual(self.step(1, "left"), 0)
        self.assertEqual(self.step(1, "right"), 2)
        self.assertIsNone(self.step(4, "left"), "the row's first tile: the highlight may leave")
        self.assertIsNone(self.step(3, "right"), "the row's last tile does not wrap to the next row")
        self.assertIsNone(self.step(9, "right"), "the last tile of all")

    def test_up_and_down_move_between_rows_and_stop_at_the_first_and_last(self):
        self.assertEqual(self.step(5, "up"), 1)
        self.assertEqual(self.step(1, "down"), 5)
        self.assertIsNone(self.step(2, "up"), "top row: up leaves for the sub-tabs")
        self.assertIsNone(self.step(8, "down"), "bottom row: down leaves for the buttons")

    def test_down_above_a_gap_in_a_short_last_row_lands_on_the_last_tile(self):
        self.assertEqual(self.step(6, "down"), 9)
        self.assertEqual(self.step(7, "down"), 9)
        self.assertEqual(self.step(4, "down"), 8)

    def test_one_row_and_one_column(self):
        self.assertIsNone(self.step(1, "down", count=3, columns=4))
        self.assertIsNone(self.step(1, "up", count=3, columns=4))
        self.assertEqual(self.step(1, "down", count=3, columns=1), 2)
        self.assertIsNone(self.step(1, "left", count=3, columns=1))
        self.assertIsNone(self.step(1, "right", count=3, columns=1))

    def test_nothing_sensible_to_move_from_is_no_move(self):
        self.assertIsNone(self.step(-1, "down"))
        self.assertIsNone(self.step(10, "up"))
        self.assertIsNone(apps.grid_step(0, 0, 4, "right"))
        self.assertIsNone(apps.grid_step(0, 5, 0, "right"))
        self.assertIsNone(self.step(1, "sideways"))


class FolderTests(unittest.TestCase):
    def test_the_persons_own_folder_comes_first_and_flatpak_and_snap_are_always_looked_in(self):
        dirs = apps.data_dirs({}, "/home/u")
        self.assertEqual(dirs[:3], ["/home/u/.local/share", "/usr/local/share", "/usr/share"])
        self.assertIn("/var/lib/flatpak/exports/share", dirs)
        self.assertIn("/home/u/.local/share/flatpak/exports/share", dirs)
        self.assertIn("/var/lib/snapd/desktop", dirs)

    def test_the_sessions_own_list_is_used_without_repeats(self):
        dirs = apps.data_dirs({"XDG_DATA_HOME": "/data", "XDG_DATA_DIRS": "/a/:/var/lib/flatpak/exports/share::/a"},
                              "/home/u")
        self.assertEqual(dirs[:3], ["/data", "/a", "/var/lib/flatpak/exports/share"])
        self.assertEqual(len(dirs), len(set(dirs)))
        self.assertNotIn("", dirs)


class StartingTests(unittest.TestCase):
    def game(self, terminal, about=""):
        return {"name": "Tetris CLI", "argv": ["/usr/games/tetris/tetris"], "terminal": terminal, "about": about}

    def test_a_windowed_program_is_run_as_its_entry_says(self):
        self.assertEqual(apps.launch_argv(self.game(False), "/usr/bin/x-terminal-emulator"), ["/usr/games/tetris/tetris"])

    def test_a_terminal_program_is_given_a_terminal(self):
        self.assertEqual(apps.launch_argv(self.game(True), "/usr/bin/x-terminal-emulator"),
                         ["/usr/bin/x-terminal-emulator", "-e", "/usr/games/tetris/tetris"])

    def test_with_no_terminal_program_a_terminal_program_is_not_started(self):
        self.assertIsNone(apps.launch_argv(self.game(True), None))

    def test_what_the_page_says_afterwards(self):
        g = self.game(True)
        self.assertEqual(apps.started_text(g), "Started Tetris CLI.")
        self.assertIn("Permission denied", apps.started_text(g, error="Permission denied"))
        self.assertIn("no terminal program", apps.started_text(g, no_terminal=True))

    def test_the_line_under_the_grid(self):
        self.assertEqual(apps.about_text(None, 0, ""), apps.NONE_FOUND)
        self.assertEqual(apps.about_text(None, 3, "Games"), "Games: 3 programs")
        self.assertEqual(apps.about_text(None, 1, "Games"), "Games: 1 program")
        self.assertEqual(apps.about_text(self.game(True), 3, "Games"), "Tetris CLI")
        self.assertEqual(apps.about_text(self.game(True, "Falling blocks."), 3, "Games"), "Tetris CLI: Falling blocks.")


class IconTests(unittest.TestCase):
    DIRS = ["/home/u/.local/share", "/usr/share"]

    def test_the_largest_picture_in_the_first_folder_that_has_one(self):
        there = {"/usr/share/icons/hicolor/48x48/apps/tetris.png", "/usr/share/icons/hicolor/128x128/apps/tetris.png",
                 "/usr/share/pixmaps/tetris.xpm"}
        self.assertEqual(apps.icon_file("tetris", self.DIRS, there.__contains__),
                         "/usr/share/icons/hicolor/128x128/apps/tetris.png")

    def test_the_old_pixmaps_folder_is_the_last_place_looked(self):
        there = {"/usr/share/pixmaps/tetris.xpm"}
        self.assertEqual(apps.icon_file("tetris", self.DIRS, there.__contains__), "/usr/share/pixmaps/tetris.xpm")

    def test_a_full_path_is_used_as_it_is_when_it_is_there(self):
        self.assertEqual(apps.icon_file("/opt/g/icon.png", self.DIRS, {"/opt/g/icon.png"}.__contains__), "/opt/g/icon.png")
        self.assertIsNone(apps.icon_file("/opt/g/icon.png", self.DIRS, lambda p: False))

    def test_a_program_with_no_picture_gets_its_first_letter(self):
        self.assertEqual(apps.initial("xgps"), "X")
        self.assertEqual(apps.initial("  (beta) tool"), "B")
        self.assertEqual(apps.initial("7zip"), "7")
        self.assertEqual(apps.initial("---"), "?")
        self.assertEqual(apps.initial(""), "?")

    def test_no_name_or_no_file_is_no_picture(self):
        self.assertIsNone(apps.icon_file("", self.DIRS, lambda p: True))
        self.assertIsNone(apps.icon_file("tetris", self.DIRS, lambda p: False))


class TabTests(unittest.TestCase):
    def test_the_apps_tab_is_only_there_when_asked_for_and_sits_before_settings(self):
        self.assertNotIn("Apps", data.tab_names({}, False))
        names = data.tab_names({}, False, apps=True, wiki=True)
        self.assertEqual(names[-4:], ["Apps", "Settings", "Help", "Wiki"])
        self.assertNotIn("Games", names)

    def test_someone_who_switched_on_the_games_tab_keeps_it_as_the_apps_tab(self):
        self.assertFalse(apps.wanted({}))
        self.assertTrue(apps.wanted({"apps_tab": True}))
        self.assertTrue(apps.wanted({"games_tab": True}))
        self.assertFalse(apps.wanted({"apps_tab": False, "games_tab": False}))


if __name__ == "__main__":
    unittest.main()
