"""
IGA's slim top row, its panels, its icons, and the three full-screen states: every decision, tested without Qt.

    python3 -m unittest discover -s tests
"""
import unittest

from ops_gui import iga, style

ROW = 1260                              # about the width of the row on the uConsole's 1280 pixel screen


class RowLayoutTests(unittest.TestCase):
    def test_one_to_six_pages_at_the_uconsoles_width(self):
        buttons = len(iga.ROW_BUTTONS) * (iga.BUTTON_WIDTH + iga.ROW_GAP)
        modes = {}
        for count in range(1, 7):
            for current in range(count):
                row = iga.row_layout(ROW, count, current)
                self.assertEqual(len(row), count)
                self.assertEqual(row[current]["mode"], "address")
                self.assertGreaterEqual(row[current]["width"], iga.ADDRESS_MIN, (count, current))
                self.assertLessEqual(sum(t["width"] for t in row) + buttons + count * iga.ROW_GAP, ROW)
                others = {t["mode"] for i, t in enumerate(row) if i != current}
                self.assertLessEqual(len(others), 1)
                modes[count] = others.pop() if others else None
        self.assertIsNone(modes[1])
        self.assertEqual([modes[n] for n in (2, 3, 4)], ["title"] * 3)
        self.assertEqual(modes[6], "icon")                # with six pages the others are favicons only

    def test_titles_never_grow_past_their_most_and_icons_are_small(self):
        row = iga.row_layout(ROW, 2, 0)
        self.assertEqual(row[1], {"mode": "title", "width": iga.TITLE_TAB_MAX})
        self.assertEqual(iga.row_layout(ROW, 6, 2)[0], {"mode": "icon", "width": iga.ICON_TAB})

    def test_a_narrow_row_still_gives_every_page_a_place(self):
        row = iga.row_layout(400, 6, 5)
        self.assertEqual(len(row), 6)
        self.assertTrue(all(t["width"] >= 0 for t in row))
        self.assertEqual(iga.row_layout(ROW, 0, 9)[0]["mode"], "address")    # odd input is made sensible

    def test_characters_that_fit(self):
        self.assertEqual(iga.chars_for(iga.ICON_TAB), 0)
        self.assertGreater(iga.chars_for(iga.TITLE_TAB_MIN), 10)
        self.assertEqual(iga.chars_for(500, icons=2), (500 - 12 - 40) // 8)

    def test_the_address_puts_the_host_in_bold_escaped_and_cut(self):
        self.assertEqual(iga.address_html("https://example.org/a"), "<b>example.org</b>/a")
        self.assertEqual(iga.address_html("http://example.org/"), "http://<b>example.org</b>/")
        cut = iga.address_html("https://example.org/" + "x" * 100, most=30)
        self.assertTrue(cut.startswith("<b>example.org</b>/"))
        self.assertTrue(cut.endswith("…"))
        self.assertIn("&lt;script&gt;", iga.address_html("https://a.org/<script>"))
        self.assertNotIn("<script>", iga.address_html("https://a.org/<script>"))
        self.assertEqual(iga.address_html("about:blank"), "about:blank")

    def test_the_mark_icon(self):
        self.assertEqual(iga.mark_icon("https://a.org"), "lock")
        self.assertEqual(iga.mark_icon("http://a.org"), "unlock")
        self.assertEqual(iga.mark_icon("about:blank", own=True), "app")
        self.assertIsNone(iga.mark_icon("about:blank"))


class IconTests(unittest.TestCase):
    def test_every_icon_is_sixteen_by_sixteen_of_two_characters(self):
        for name in ("back", "forward", "reload", "stop", "new", "menu", "bookmark", "lock", "unlock", "app",
                     "page"):
            rows = iga.ICONS[name]
            self.assertEqual(len(rows), 16, name)
            self.assertTrue(all(len(r) == 16 and set(r) <= set("#.") for r in rows), name)
            self.assertIn("#", "".join(rows), name)
        self.assertEqual(iga.ICONS["forward"], [r[::-1] for r in iga.ICONS["back"]])
        self.assertEqual(len({"".join(rows) for rows in iga.ICONS.values()}), len(iga.ICONS))

    def test_every_icon_button_says_what_it_does_and_its_key(self):
        for name in iga.ROW_BUTTONS:
            self.assertIn(name, iga.ICONS)
        for name, key in (("back", "Alt+Left"), ("forward", "Alt+Right"), ("reload", "Ctrl+R"), ("new", "Ctrl+T")):
            self.assertIn(key, iga.ICON_WORDS[name])


class PanelTests(unittest.TestCase):
    def test_each_panel(self):
        self.assertEqual(iga.panel_spec("message", text=iga.TOO_MANY)["buttons"], [("ok", "OK")])
        b = iga.panel_spec("bookmark", title="Example")
        self.assertEqual((b["field"], [i for i, _ in b["buttons"]]), ("Example", ["save", "cancel"]))
        d = iga.panel_spec("download", name="example.pdf", folder="Downloads")
        self.assertEqual(d["text"], "Save example.pdf to Downloads?")
        self.assertEqual([w for _, w in d["buttons"]], ["Save", "Choose folder", "Cancel"])
        f = iga.panel_spec("folders", name="a.pdf", folder="/home/u", entries=[("Downloads/", "/home/u/Downloads")])
        self.assertEqual(f["list"], ["Downloads/"])
        self.assertEqual([i for i, _ in iga.panel_spec("find")["buttons"]], ["next", "previous", "close"])
        self.assertIsNone(iga.panel_spec("bookmarks", bookmarks=[])["list"])
        marks = iga.panel_spec("bookmarks", bookmarks=[{"title": "A", "url": "https://a.org/"}])
        self.assertEqual(marks["list"], ["A  (https://a.org/)"])
        self.assertIn("Remove the bookmark 'A'?", iga.panel_spec("remove", title="A")["text"])
        self.assertIn("Bookmarks are kept", iga.panel_spec("clear")["text"])
        self.assertEqual(iga.panel_spec("downloads", lines=[])["list"], None)
        with self.assertRaises(ValueError):
            iga.panel_spec("nonsense")

    def test_the_menu_is_buttons_for_every_action(self):
        ids = [i for i, _ in iga.panel_spec("menu")["buttons"]]
        for want in ("new", "close_page", "start", "bookmarks", "downloads", "find", "zoom_in", "zoom_out",
                     "zoom_reset", "clear", "close"):
            self.assertIn(want, ids)

    def test_the_folder_chooser(self):
        names = ["Music", ".cache", "notes.txt", "Downloads", "documents"]
        rows = iga.folder_entries("/home/u", names, lambda p: not p.endswith(".txt"))
        self.assertEqual(rows, [(".. (up one folder)", "/home"), ("documents/", "/home/u/documents"),
                                ("Downloads/", "/home/u/Downloads"), ("Music/", "/home/u/Music")])
        self.assertEqual(iga.folder_entries("/", [], lambda p: True), [])

    def test_downloads_wait_in_igas_own_folder_until_you_choose(self):
        self.assertTrue(iga.pending_folder("/home/u").startswith(iga.user_folder("/home/u")))
        self.assertEqual(iga.download_next("going", None, False), "wait")
        self.assertEqual(iga.download_next("done", None, False), "wait")
        self.assertEqual(iga.download_next("done", "/home/u/Downloads/a.pdf", False), "move")
        self.assertEqual(iga.download_next("cancelled", None, False), "delete")
        self.assertEqual(iga.download_next("failed", "/x", False), "delete")
        self.assertIsNone(iga.download_next("done", "/x", True))


class EscapeTests(unittest.TestCase):
    def test_escape_closes_a_panel_and_folds_the_field_first(self):
        self.assertEqual(iga.escape_step(True, True, "page", panel=True, editing=True, fullscreen=2), "panel")
        self.assertEqual(iga.escape_step(False, True, "address", editing=True, fullscreen=2), "collapse")

    def test_full_screen_steps_back_after_page_level_steps(self):
        self.assertEqual(iga.escape_step(False, True, "page", fullscreen=2), "stop")
        self.assertEqual(iga.escape_step(False, False, "page", fullscreen=2), "fullscreen")
        self.assertEqual(iga.escape_step(False, False, "address", fullscreen=1), "fullscreen")
        self.assertEqual(iga.escape_step(False, False, None, fullscreen=1), "fullscreen")
        self.assertEqual(iga.escape_step(False, False, "page", fullscreen=0), "address")


class FullScreenTests(unittest.TestCase):
    def test_the_button_cycles_and_back_steps(self):
        self.assertEqual([iga.next_fullscreen(s, "toggle") for s in (0, 1, 2)], [1, 2, 0])
        self.assertEqual([iga.next_fullscreen(s, "back") for s in (0, 1, 2)], [0, 0, 1])
        self.assertEqual([iga.next_fullscreen(s, "leave") for s in (0, 1, 2)], [0, 0, 0])
        self.assertEqual(iga.next_fullscreen("odd", "toggle"), 1)
        self.assertEqual(iga.next_fullscreen(1, "nonsense"), 1)

    def test_a_page_asking_for_full_screen(self):
        self.assertEqual(iga.next_fullscreen(0, "page_on"), 2)
        self.assertEqual(iga.next_fullscreen(2, "page_off", before=1), 1)
        self.assertEqual(iga.next_fullscreen(2, "page_off", before=0), 0)
        self.assertEqual(iga.next_fullscreen(2, "page_off", before=2), 0)

    def test_what_each_state_shows(self):
        self.assertEqual(iga.fullscreen_view(0), {"window_full": False, "app_bars": True, "iga_row": True})
        self.assertEqual(iga.fullscreen_view(1), {"window_full": True, "app_bars": False, "iga_row": True})
        self.assertEqual(iga.fullscreen_view(2), {"window_full": True, "app_bars": False, "iga_row": False})

    def test_f11(self):
        self.assertEqual(iga.f11_action(0), "toggle")
        self.assertEqual(iga.f11_action(1), "back")
        self.assertEqual(iga.f11_action(2), "back")

    def test_the_strip_at_the_top_edge(self):
        shown, count, act = iga.top_peek_step(False, 0, 0)
        self.assertEqual((shown, act), (False, None))
        shown, count, act = iga.top_peek_step(shown, count, 1)
        self.assertEqual((shown, act), (True, "show"))
        self.assertEqual(iga.top_peek_step(True, 0, 30)[2], None)
        self.assertEqual(iga.top_peek_step(True, 0, 200)[2], "hide")
        self.assertEqual(iga.top_peek_step(True, 0, 5, inside=False)[2], "hide")
        self.assertEqual(iga.top_peek_step(False, 1, 300), (False, 0, None))   # passing the edge is not resting


class FaviconTests(unittest.TestCase):
    def test_only_a_png_data_address_is_used(self):
        ok = "data:image/png;base64,iVBORw0KGgo="
        self.assertTrue(iga.favicon_ok(ok))
        for bad in ("https://a.org/favicon.ico", "data:image/svg+xml;base64,PHN2Zz4=", 'data:image/png;base64,"><x',
                    None, "data:image/png;base64," + "A" * 30000):
            self.assertFalse(iga.favicon_ok(bad), bad)
        self.assertEqual(iga.site_key("https://Example.org/a"), "example.org")

    def test_the_start_page_shows_a_seen_favicon_beside_its_bookmark(self):
        ok = "data:image/png;base64,iVBORw0KGgo="
        page = iga.start_page([{"title": "A", "url": "https://a.org/"}, {"title": "B", "url": "https://b.org/"}],
                              style.TOKENS["dark"], icons={"a.org": ok, "b.org": "javascript:alert(1)"})
        self.assertIn(f'<img src="{ok}"', page)
        self.assertEqual(page.count("<img"), 1)
        self.assertIn("img-src data:", page)


if __name__ == "__main__":
    unittest.main()

