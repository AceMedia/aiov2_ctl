"""The Games tab's plain parts (ops_gui/games.py): finding games in the menu and what is run to start one."""
import unittest

from ops_gui import data, games

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


def tree(files):
    """listdir, read and which over a pretend disk: {path: text}; every program is found unless named in gone."""
    def listdir(folder):
        names = [p[len(folder) + 1:] for p in files if p.startswith(folder + "/") and "/" not in p[len(folder) + 1:]]
        if not names:
            raise FileNotFoundError(folder)
        return names
    return listdir, files.get


def found(files, gone=()):
    listdir, read = tree(files)
    dirs = ["/home/u/.local/share", "/usr/share", "/var/lib/flatpak/exports/share"]
    return games.find_games(dirs, listdir, read, lambda program: None if program in gone else program)


class EntryTests(unittest.TestCase):
    def test_only_the_main_part_is_read_and_translations_are_left(self):
        entry = games.parse_entry("# note\n[Other]\nName=No\n" + TETRIS + "[Desktop Action x]\nName=Also no\n")
        self.assertEqual(entry["Name"], "Tetris CLI")
        self.assertEqual(entry["Exec"], "/usr/games/tetris/tetris")
        self.assertNotIn("Name[de]", entry)

    def test_a_game_is_a_shown_program_in_the_games_category(self):
        self.assertTrue(games.is_game(games.parse_entry(TETRIS)))
        self.assertTrue(games.is_game(games.parse_entry(SNES)))
        self.assertFalse(games.is_game(games.parse_entry(EDITOR)))

    def test_a_category_that_only_contains_the_word_is_not_enough(self):
        self.assertFalse(games.is_game({"Name": "x", "Exec": "x", "Categories": "GameTool;BlocksGame;"}))

    def test_hidden_entries_links_and_ones_with_nothing_to_run_are_not_games(self):
        base = games.parse_entry(TETRIS)
        self.assertFalse(games.is_game(dict(base, NoDisplay="true")))
        self.assertFalse(games.is_game(dict(base, Hidden="True")))
        self.assertFalse(games.is_game(dict(base, Type="Link")))
        self.assertFalse(games.is_game({k: v for k, v in base.items() if k != "Exec"}))
        self.assertFalse(games.is_game({k: v for k, v in base.items() if k != "Name"}))


class CommandTests(unittest.TestCase):
    def test_a_plain_command_is_kept(self):
        self.assertEqual(games.command("/usr/games/tetris/tetris"), ["/usr/games/tetris/tetris"])

    def test_the_place_for_a_file_is_taken_out(self):
        self.assertEqual(games.command("/usr/bin/steamlink %u"), ["/usr/bin/steamlink"])

    def test_flatpaks_wrapping_goes_with_it(self):
        self.assertEqual(games.command(games.parse_entry(SNES)["Exec"]),
                         ["/usr/bin/flatpak", "run", "--branch=stable", "--arch=aarch64", "--command=snes9x-gtk",
                          "--file-forwarding", "com.snes9x.Snes9x"])

    def test_quotes_a_real_percent_sign_and_an_empty_argument_survive(self):
        self.assertEqual(games.command('game "two words" --rate=50%% "" %i'), ["game", "two words", "--rate=50%", ""])

    def test_a_line_that_cannot_be_read_gives_nothing(self):
        self.assertEqual(games.command('game "unfinished'), [])
        self.assertEqual(games.command(""), [])
        self.assertEqual(games.command(None), [])


class FindingTests(unittest.TestCase):
    def test_games_are_found_in_every_folder_and_listed_by_name(self):
        got = found({"/usr/share/applications/tetris.desktop": TETRIS,
                     "/usr/share/applications/steamlink.desktop": STEAMLINK,
                     "/usr/share/applications/editor.desktop": EDITOR,
                     "/usr/share/applications/notes.txt": TETRIS,
                     "/var/lib/flatpak/exports/share/applications/com.snes9x.Snes9x.desktop": SNES})
        self.assertEqual([g["name"] for g in got], ["Snes9x", "Steam Link", "Tetris CLI"])
        tetris = got[2]
        self.assertEqual((tetris["id"], tetris["terminal"], tetris["icon"], tetris["about"]),
                         ("tetris", True, "tetris", "Ascii/cli rendition of tetris."))
        self.assertFalse(got[1]["terminal"])

    def test_a_persons_own_entry_wins_over_the_systems_and_can_hide_it(self):
        mine = TETRIS.replace("Name=Tetris CLI", "Name=My Tetris")
        got = found({"/home/u/.local/share/applications/tetris.desktop": mine,
                     "/usr/share/applications/tetris.desktop": TETRIS})
        self.assertEqual([g["name"] for g in got], ["My Tetris"])
        got = found({"/home/u/.local/share/applications/tetris.desktop": TETRIS + "Hidden=true\n",
                     "/usr/share/applications/tetris.desktop": TETRIS})
        self.assertEqual(got, [])

    def test_a_game_whose_program_has_gone_is_not_listed(self):
        files = {"/usr/share/applications/tetris.desktop": TETRIS,
                 "/usr/share/applications/steamlink.desktop": STEAMLINK + "TryExec=steamlink-check\n"}
        self.assertEqual([g["name"] for g in found(files, gone=("/usr/games/tetris/tetris",))], ["Steam Link"])
        self.assertEqual([g["name"] for g in found(files, gone=("steamlink-check",))], ["Tetris CLI"])

    def test_no_folders_at_all_is_no_games_not_an_error(self):
        self.assertEqual(found({}), [])

    def test_a_file_that_cannot_be_read_is_passed_over(self):
        listdir = lambda folder: ["a.desktop"] if folder == "/usr/share/applications" else (_ for _ in ()).throw(OSError())
        self.assertEqual(games.find_games(["/usr/share"], listdir, lambda path: None, lambda p: p), [])


class FolderTests(unittest.TestCase):
    def test_the_persons_own_folder_comes_first_and_flatpak_and_snap_are_always_looked_in(self):
        dirs = games.data_dirs({}, "/home/u")
        self.assertEqual(dirs[:3], ["/home/u/.local/share", "/usr/local/share", "/usr/share"])
        self.assertIn("/var/lib/flatpak/exports/share", dirs)
        self.assertIn("/home/u/.local/share/flatpak/exports/share", dirs)
        self.assertIn("/var/lib/snapd/desktop", dirs)

    def test_the_sessions_own_list_is_used_without_repeats(self):
        dirs = games.data_dirs({"XDG_DATA_HOME": "/data", "XDG_DATA_DIRS": "/a/:/var/lib/flatpak/exports/share::/a"},
                               "/home/u")
        self.assertEqual(dirs[:3], ["/data", "/a", "/var/lib/flatpak/exports/share"])
        self.assertEqual(len(dirs), len(set(dirs)))
        self.assertNotIn("", dirs)


class StartingTests(unittest.TestCase):
    def game(self, terminal):
        return {"name": "Tetris CLI", "argv": ["/usr/games/tetris/tetris"], "terminal": terminal}

    def test_a_windowed_game_is_run_as_its_entry_says(self):
        self.assertEqual(games.launch_argv(self.game(False), "/usr/bin/x-terminal-emulator"), ["/usr/games/tetris/tetris"])

    def test_a_terminal_game_is_given_a_terminal(self):
        self.assertEqual(games.launch_argv(self.game(True), "/usr/bin/x-terminal-emulator"),
                         ["/usr/bin/x-terminal-emulator", "-e", "/usr/games/tetris/tetris"])

    def test_with_no_terminal_program_a_terminal_game_is_not_started(self):
        self.assertIsNone(games.launch_argv(self.game(True), None))

    def test_what_the_page_says_afterwards(self):
        g = self.game(True)
        self.assertEqual(games.started_text(g), "Started Tetris CLI.")
        self.assertIn("Permission denied", games.started_text(g, error="Permission denied"))
        self.assertIn("no terminal program", games.started_text(g, no_terminal=True))

    def test_the_count_line(self):
        self.assertEqual(games.summary(0), games.NONE_FOUND)
        self.assertEqual(games.summary(1), "1 game")
        self.assertEqual(games.summary(7), "7 games")


class IconTests(unittest.TestCase):
    DIRS = ["/home/u/.local/share", "/usr/share"]

    def test_the_largest_picture_in_the_first_folder_that_has_one(self):
        there = {"/usr/share/icons/hicolor/48x48/apps/tetris.png", "/usr/share/icons/hicolor/128x128/apps/tetris.png",
                 "/usr/share/pixmaps/tetris.xpm"}
        self.assertEqual(games.icon_file("tetris", self.DIRS, there.__contains__),
                         "/usr/share/icons/hicolor/128x128/apps/tetris.png")

    def test_the_old_pixmaps_folder_is_the_last_place_looked(self):
        there = {"/usr/share/pixmaps/tetris.xpm"}
        self.assertEqual(games.icon_file("tetris", self.DIRS, there.__contains__), "/usr/share/pixmaps/tetris.xpm")

    def test_a_full_path_is_used_as_it_is_when_it_is_there(self):
        self.assertEqual(games.icon_file("/opt/g/icon.png", self.DIRS, {"/opt/g/icon.png"}.__contains__), "/opt/g/icon.png")
        self.assertIsNone(games.icon_file("/opt/g/icon.png", self.DIRS, lambda p: False))

    def test_no_name_or_no_file_is_no_picture(self):
        self.assertIsNone(games.icon_file("", self.DIRS, lambda p: True))
        self.assertIsNone(games.icon_file("tetris", self.DIRS, lambda p: False))


class TabTests(unittest.TestCase):
    def test_the_games_tab_is_only_there_when_asked_for_and_sits_before_settings(self):
        self.assertNotIn("Games", data.tab_names({}, False))
        names = data.tab_names({}, False, games=True, wiki=True)
        self.assertEqual(names[-4:], ["Games", "Settings", "Help", "Wiki"])


if __name__ == "__main__":
    unittest.main()
