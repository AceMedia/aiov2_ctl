"""
IGA's slim top row, its panels, its icons, and the three full-screen states: every decision, tested without Qt.

    python3 -m unittest discover -s tests
"""
import unittest

from ops_gui import iga, style

ROW = 1260                              # about the width of the row on the uConsole's 1280 pixel screen


class RowLayoutTests(unittest.TestCase):
    def test_the_strip_is_what_the_six_buttons_and_six_gaps_leave(self):
        self.assertEqual(iga.strip_width(ROW), 1068)

    def test_one_to_six_pages_at_the_uconsoles_width(self):
        want = {1: ([], 1068), 2: ([196], 872), 3: ([196] * 2, 676), 4: ([196] * 3, 480), 5: ([162] * 4, 420),
                6: ([40] * 5, 868)}
        for count in range(1, 7):
            for current in range(count):
                row = iga.row_layout(ROW, count, current)
                self.assertEqual(sum(t["width"] for t in row), 1068, (count, current))   # no gaps between tabs
                self.assertEqual(row[current], {"mode": "address", "width": want[count][1]}, (count, current))
                others = [t["width"] for i, t in enumerate(row) if i != current]
                self.assertEqual(others, want[count][0], (count, current))
                self.assertGreaterEqual(row[current]["width"], iga.ADDRESS_MIN)
        self.assertEqual({t["mode"] for t in iga.row_layout(ROW, 6, 0)[1:]}, {"icon"})
        self.assertEqual({t["mode"] for t in iga.row_layout(ROW, 5, 0)[1:]}, {"title"})

    def test_a_narrow_row_still_gives_every_page_a_place(self):
        row = iga.row_layout(400, 6, 5)
        self.assertEqual(len(row), 6)
        self.assertTrue(all(t["width"] >= 0 for t in row))
        self.assertEqual(iga.row_layout(ROW, 0, 9)[0]["mode"], "address")    # odd input is made sensible

    def test_the_overlap_is_the_styles(self):
        self.assertEqual(iga.TAB_OVERLAP, 4)
        self.assertIn(f"margin: 4px -{iga.TAB_OVERLAP}px 0px 0px;", style.stylesheet("dark"))


class AddressSlotTests(unittest.TestCase):
    def test_mark_favicon_and_words_in_order_centred_and_clear_of_the_foot(self):
        s = iga.address_slots(872)
        self.assertEqual(s["mark"], (10, 6, 16, 16))
        self.assertEqual(s["favicon"], (30, 6, 16, 16))
        self.assertEqual(s["text"], (52, 6, 872 - 52 - 14, 16))
        for x, y, w, h in s.values():
            self.assertEqual(y + h / 2, iga.CENTRE_LINE)
            self.assertLessEqual(x + w, 872 - iga.TAB_INSET_RIGHT)

    def test_while_typing_there_is_no_favicon_and_the_box_is_inside_the_tab(self):
        s = iga.address_slots(1260, editing=True)
        self.assertIsNone(s["favicon"])
        self.assertEqual(s["text"][0], 32)
        x, y, w, h = iga.editor_box(1260)
        self.assertEqual((x, y + h / 2), (32, iga.CENTRE_LINE))
        self.assertLessEqual(y + h, iga.ROW_HEIGHT)
        self.assertGreaterEqual(y, 0)
        self.assertEqual(iga.address_slots(10)["text"][2], 0)      # never a negative width


class AddressTextTests(unittest.TestCase):
    def test_runs(self):
        self.assertEqual(iga.address_runs("https://example.org/a?b=1"), ("", "example.org", "/a?b=1"))
        self.assertEqual(iga.address_runs("http://192.168.0.1:8080/x"), ("http://", "192.168.0.1:8080", "/x"))
        self.assertEqual(iga.address_runs("about:blank"), ("", "", "about:blank"))

    def test_the_path_goes_first_and_the_host_is_kept(self):
        fits = lambda s: len(s) <= 20  # noqa: E731
        self.assertEqual(iga.elide_address("", "example.org", "/a", fits), ("", "example.org", "/a"))
        lead, host, rest = iga.elide_address("", "example.org", "/a/very/long/path", fits)
        self.assertEqual(host, "example.org")
        self.assertTrue(rest.endswith("…"))
        self.assertLessEqual(len(lead + host + rest), 20)
        lead, host, rest = iga.elide_address("http://", "a-very-long-host-name.example.org", "/x", fits)
        self.assertEqual(rest, "")
        self.assertTrue(host.endswith("…"))
        self.assertEqual(iga.elide_address("", "abc", "", lambda s: False), ("", "…", ""))

    def test_the_label_uses_colour_not_weight_and_is_escaped(self):
        t = style.TOKENS["dark"]
        text = iga.address_label("", "a.org", "/<b>", t)
        self.assertIn(f"color:{t['text']}\">a.org</span>", text)
        self.assertIn(f"color:{t['muted']}\">/&lt;b&gt;</span>", text)
        self.assertNotIn("<b>", text)

    def test_the_mark(self):
        self.assertEqual(iga.mark_icon("https://a.org"), "lock")
        self.assertEqual(iga.mark_icon("http://a.org"), "unlock")
        self.assertEqual(iga.mark_icon("about:blank", own=True), "app")
        self.assertIsNone(iga.mark_icon("about:blank"))
        self.assertIn("does not vouch for the site", iga.mark_words("https://a.org"))
        self.assertIn("can be read", iga.mark_words("http://a.org"))


class FieldHeightTests(unittest.TestCase):
    def test_the_address_field_drops_the_general_minimum_and_padding_in_every_look(self):
        for mode in ("dark", "light"):
            sheet = style.stylesheet(mode)
            rule = "QLineEdit#rowfield { min-height: 0px; padding-top: 0px; padding-bottom: 0px; }"
            self.assertIn(rule, sheet, mode)
            self.assertGreater(sheet.index(rule), sheet.rindex("QLineEdit, QComboBox, QAbstractSpinBox {"))
            self.assertGreater(sheet.index(rule), sheet.rindex("QLineEdit {{ min-height".replace("{{", "{"))
                               if "QLineEdit { min-height" in sheet else 0)

    def test_the_tab_uses_it_at_the_rows_height(self):
        import os
        with open(os.path.join(os.path.dirname(iga.__file__), "iga.py")) as f:
            text = f.read()
        self.assertEqual(text.count('setObjectName("rowfield")'), 2)
        self.assertEqual(iga.ROW_HEIGHT, 28)


class IconTests(unittest.TestCase):
    NAMES = ("back", "forward", "reload", "stop", "new", "menu", "bookmark", "lock", "unlock", "app", "page")

    def test_every_icon_is_sixteen_by_sixteen_of_two_characters(self):
        for name in self.NAMES:
            rows = iga.ICONS[name]
            self.assertEqual(len(rows), 16, name)
            self.assertTrue(all(len(r) == 16 and set(r) <= set("#.") for r in rows), name)
        self.assertEqual(iga.ICONS["forward"], [r[::-1] for r in iga.ICONS["back"]])
        self.assertEqual(len({"".join(rows) for rows in iga.ICONS.values()}), len(iga.ICONS))

    def test_the_same_weight_and_centre_for_all(self):
        from ops_gui import pixelicon
        for name in self.NAMES:
            rows = iga.ICONS[name]
            left, top, right, bottom = pixelicon.ink_bounds(rows)
            self.assertTrue(1 <= left and right <= 14 and top == 2 or name == "menu", (name, left, top))
            self.assertTrue(1 <= left and right <= 14 and 2 <= top and bottom <= 13, name)
            self.assertLessEqual(abs((left + right) / 2 - 7.5), 0.5, name)
            self.assertLessEqual(abs((top + bottom) / 2 - 7.5), 0.5, name)
            self.assertEqual(pixelicon.lone_pixels(rows), [], name)         # strokes are never one pixel alone

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

