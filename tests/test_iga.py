"""
IGA, the optional browser tab: everything it decides, tested without Qt. The suite never imports Qt WebEngine.

    python3 -m unittest discover -s tests
"""
import json
import os
import stat
import sys
import tempfile
import unittest

from ops_gui import iga, style

DDG = iga.DUCKDUCKGO


class SwitchTests(unittest.TestCase):
    def test_on_only_with_the_switch_file(self):
        self.assertTrue(iga.wanted("/f", isfile=lambda p: p == "/f/enabled"))
        self.assertFalse(iga.wanted("/f", isfile=lambda p: False))
        self.assertTrue(iga.user_folder("/home/u").endswith("/home/u/.local/share/aiov2_ctl/iga"))
        self.assertEqual(iga.profile_folder("/home/u"), "/home/u/.local/share/aiov2_ctl/iga/profile")

    def test_importing_this_module_does_not_import_qt_webengine(self):
        self.assertNotIn("PyQt6.QtWebEngineWidgets", sys.modules)
        self.assertNotIn("PyQt6.QtWebEngineCore", sys.modules)

    def test_a_missing_engine_is_explained_with_the_command_that_fixes_it(self):
        text = iga.engine_missing_text(ImportError("No module named 'PyQt6.QtWebEngineWidgets'"))
        self.assertIn("sudo apt install python3-pyqt6.qtwebengine", text)
        self.assertIn("QtWebEngineWidgets", text)
        self.assertIn("rest of the app", text)
        self.assertIn("ImportError", iga.engine_missing_text(ImportError()))


class AddressTests(unittest.TestCase):
    def test_an_empty_box_does_nothing(self):
        self.assertIsNone(iga.to_url(""))
        self.assertIsNone(iga.to_url("   "))
        self.assertIsNone(iga.to_url(None, DDG))

    def test_an_address_with_a_scheme_is_kept_as_it_is(self):
        self.assertEqual(iga.to_url("https://example.org/a?b=c"), ("url", "https://example.org/a?b=c"))
        self.assertEqual(iga.to_url("  http://example.org  "), ("url", "http://example.org"))
        self.assertEqual(iga.to_url("https://"), ("refuse", "That address has no site name after the //."))

    def test_blank_and_the_start_page_are_allowed(self):
        self.assertEqual(iga.to_url("about:blank"), ("url", "about:blank"))
        self.assertEqual(iga.to_url("IGA:start"), ("url", iga.START))

    def test_other_schemes_are_refused_with_a_plain_reason(self):
        kind, why = iga.to_url("file:///etc/passwd")
        self.assertEqual(kind, "refuse")
        self.assertIn("files on this device", why)
        for text in ("javascript:alert(1)", "data:text/html,hi", "chrome://settings", "view-source:https://a.b",
                     "ftp://x.org", "blob:abc"):
            kind, why = iga.to_url(text, DDG)
            self.assertEqual(kind, "refuse", text)
            self.assertIn("only web addresses", why)

    def test_a_bare_host_gets_https(self):
        self.assertEqual(iga.to_url("example.org"), ("url", "https://example.org"))
        self.assertEqual(iga.to_url("www.example.org/a/b?c=1"), ("url", "https://www.example.org/a/b?c=1"))
        self.assertEqual(iga.to_url("example.org:8443/x"), ("url", "https://example.org:8443/x"))
        self.assertEqual(iga.to_url("8.8.8.8"), ("url", "https://8.8.8.8"))

    def test_localhost_and_private_addresses_get_http(self):
        self.assertEqual(iga.to_url("localhost"), ("url", "http://localhost"))
        self.assertEqual(iga.to_url("localhost:8080/x"), ("url", "http://localhost:8080/x"))
        self.assertEqual(iga.to_url("192.168.0.10"), ("url", "http://192.168.0.10"))
        self.assertEqual(iga.to_url("10.1.2.3:3000"), ("url", "http://10.1.2.3:3000"))
        self.assertEqual(iga.to_url("172.16.0.1"), ("url", "http://172.16.0.1"))
        self.assertEqual(iga.to_url("127.0.0.1:5000"), ("url", "http://127.0.0.1:5000"))
        self.assertEqual(iga.to_url("printer.local"), ("url", "http://printer.local"))

    def test_a_bad_port_is_refused(self):
        self.assertEqual(iga.to_url("example.org:99999")[0], "refuse")

    def test_words_are_refused_when_no_search_engine_is_chosen(self):
        for text in ("how tall is a giraffe", "giraffe", "3.14", "a.b c"):
            self.assertEqual(iga.to_url(text), ("refuse", iga.NOT_ADDRESS), text)
        self.assertEqual(iga.NOT_ADDRESS, "That is not an address. Choose a search engine in Settings > IGA "
                                          "to search from the address bar.")

    def test_words_go_to_the_chosen_engine_only_percent_encoded(self):
        self.assertEqual(iga.to_url("tall giraffe & co", DDG),
                         ("search", "https://duckduckgo.com/?q=tall+giraffe+%26+co"))
        self.assertEqual(iga.to_url("ninja", "https://search.example/find?w=%s&x=1"),
                         ("search", "https://search.example/find?w=ninja&x=1"))
        self.assertEqual(iga.to_url("example.org", DDG), ("url", "https://example.org"))


class SearchSettingTests(unittest.TestCase):
    def test_no_search_is_the_default(self):
        s = iga.settings_from({})
        self.assertEqual(s, {"iga_search": "none", "iga_search_custom": "", "iga_history": False,
                             "iga_clear_on_close": False})
        self.assertIsNone(iga.search_template(s["iga_search"], s["iga_search_custom"]))
        self.assertEqual(iga.SEARCH_CHOICES[0], ("none", "No search (addresses only)"))

    def test_odd_saved_values_fall_back(self):
        s = iga.settings_from({"iga_search": "google", "iga_history": "yes", "iga_clear_on_close": 1})
        self.assertEqual(s["iga_search"], "none")
        self.assertFalse(s["iga_history"])
        self.assertFalse(s["iga_clear_on_close"])
        self.assertEqual(iga.settings_from(None), iga.DEFAULTS)

    def test_duckduckgo_and_custom(self):
        self.assertEqual(iga.search_template("duckduckgo"), DDG)
        self.assertEqual(iga.search_template("custom", "https://s.example/?q=%s"), "https://s.example/?q=%s")
        for bad in ("https://s.example/?q=", "javascript:%s", "s.example/?q=%s", "https://s.example/?q=%s x", ""):
            self.assertIsNone(iga.search_template("custom", bad), bad)


class MarksTests(unittest.TestCase):
    def test_the_address_bar_mark(self):
        self.assertEqual(iga.security_mark("https://a.org"), "secure connection")
        self.assertEqual(iga.security_mark("http://a.org"), "not secure")
        self.assertEqual(iga.security_mark(iga.START), "this app's own page")
        self.assertEqual(iga.security_mark("about:blank"), "")

    def test_the_tab_label(self):
        self.assertEqual(iga.page_label("Example Domain", "https://example.org"), "Example Domain")
        self.assertEqual(iga.page_label("", "https://www.example.org/x"), "example.org")
        self.assertEqual(iga.page_label("", "about:blank"), "New page")
        self.assertEqual(iga.page_label("", iga.START), "Start")
        long = iga.page_label("A very long title of a page indeed", "https://a.org", most=12)
        self.assertEqual(len(long), 12)
        self.assertTrue(long.endswith("…"))

    def test_shown_address(self):
        self.assertEqual(iga.shown_address("data:text/html,x"), iga.START)
        self.assertEqual(iga.shown_address("https://a.org/"), "https://a.org/")


class KeyTests(unittest.TestCase):
    def test_escape_goes_back_one_level_at_a_time(self):
        self.assertEqual(iga.escape_step(True, True, "page"), "find")
        self.assertEqual(iga.escape_step(False, True, "page"), "stop")
        self.assertEqual(iga.escape_step(False, False, "page"), "address")
        self.assertEqual(iga.escape_step(False, False, "address"), "tabs")
        self.assertIsNone(iga.escape_step(False, False, "elsewhere"))

    def test_a_page_keeps_letters_numbers_and_tab_but_not_the_way_out(self):
        self.assertTrue(iga.page_keeps(False, False, False, False, False))
        self.assertFalse(iga.page_keeps(True, False, False, False, False))
        self.assertFalse(iga.page_keeps(False, True, False, False, False))
        self.assertFalse(iga.page_keeps(False, False, True, False, False))
        self.assertFalse(iga.page_keeps(False, False, False, True, False))

    def test_zoom_steps(self):
        self.assertEqual(iga.zoom_step(1.0, 1), 1.1)
        self.assertEqual(iga.zoom_step(1.0, -1), 0.9)
        self.assertEqual(iga.zoom_step(3.0, 1), 3.0)
        self.assertEqual(iga.zoom_step(0.5, -1), 0.5)
        self.assertEqual(iga.zoom_step(2.0, 0), 1.0)


class PagesTests(unittest.TestCase):
    def test_it_starts_with_one_page(self):
        p = iga.Pages()
        self.assertEqual(len(p.order), 1)
        self.assertEqual(p.current, p.order[0])

    def test_a_new_page_opens_after_the_current_one(self):
        p = iga.Pages()
        a = p.current
        b, _ = p.open()
        p.select(a)
        c, _ = p.open()
        self.assertEqual(p.order, [a, c, b])
        self.assertEqual(p.current, c)

    def test_the_seventh_is_refused(self):
        p = iga.Pages()
        for _ in range(5):
            self.assertIsNotNone(p.open()[0])
        pid, why = p.open()
        self.assertIsNone(pid)
        self.assertEqual(why, "Six pages is the most IGA keeps open, to save memory. Close one first.")
        self.assertEqual(len(p.order), 6)

    def test_closing_moves_to_a_neighbour_and_the_last_leaves_a_blank_page(self):
        p = iga.Pages()
        a = p.current
        b, _ = p.open()
        c, _ = p.open()
        p.select(b)
        self.assertIsNone(p.close(b))
        self.assertEqual(p.current, c)
        p.close(c)
        self.assertEqual(p.current, a)
        new = p.close(a)
        self.assertIsNotNone(new)
        self.assertEqual(p.order, [new])
        self.assertEqual(p.current, new)
        self.assertIsNone(p.close(12345))

    def test_move_and_step(self):
        p = iga.Pages()
        a = p.current
        b, _ = p.open()
        c, _ = p.open()
        p.move(c, 0)
        self.assertEqual(p.order, [c, a, b])
        p.select(b)
        self.assertEqual(p.step(1), c)
        self.assertEqual(p.step(-1), b)
        self.assertEqual(p.index(a), 1)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "aiov2_ctl", "iga")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_list_rename_remove(self):
        self.assertEqual(iga.load_bookmarks(self.folder), [])
        self.assertTrue(iga.add_bookmark(self.folder, "Example", "https://example.org/"))
        self.assertTrue(iga.add_bookmark(self.folder, "", "http://192.168.0.1/"))
        self.assertTrue(iga.add_bookmark(self.folder, "Example again", "https://example.org/"))
        self.assertEqual(iga.load_bookmarks(self.folder),
                         [{"title": "Example again", "url": "https://example.org/"},
                          {"title": "http://192.168.0.1/", "url": "http://192.168.0.1/"}])
        self.assertTrue(iga.rename_bookmark(self.folder, "http://192.168.0.1/", "Router"))
        self.assertFalse(iga.rename_bookmark(self.folder, "https://nope.org/", "x"))
        self.assertTrue(iga.remove_bookmark(self.folder, "https://example.org/"))
        self.assertFalse(iga.remove_bookmark(self.folder, "https://example.org/"))
        self.assertEqual(iga.load_bookmarks(self.folder), [{"title": "Router", "url": "http://192.168.0.1/"}])

    def test_only_web_addresses_are_kept(self):
        for url in ("javascript:alert(1)", "file:///etc", "about:blank", iga.START, ""):
            self.assertFalse(iga.add_bookmark(self.folder, "x", url))

    def test_the_file_and_folder_are_private(self):
        iga.add_bookmark(self.folder, "Example", "https://example.org/")
        self.assertEqual(mode(self.folder), 0o700)
        self.assertEqual(mode(iga.bookmarks_path(self.folder)), 0o600)

    def test_a_corrupt_file_is_empty_and_left_until_one_is_added(self):
        os.makedirs(self.folder)
        path = iga.bookmarks_path(self.folder)
        with open(path, "w") as f:
            f.write("{not json")
        self.assertEqual(iga.load_bookmarks(self.folder), [])
        self.assertFalse(iga.remove_bookmark(self.folder, "https://example.org/"))
        self.assertFalse(iga.rename_bookmark(self.folder, "https://example.org/", "x"))
        with open(path) as f:
            self.assertEqual(f.read(), "{not json")
        iga.add_bookmark(self.folder, "Example", "https://example.org/")
        with open(path) as f:
            self.assertEqual(json.load(f), [{"title": "Example", "url": "https://example.org/"}])


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "iga")

    def tearDown(self):
        self.tmp.cleanup()

    def test_nothing_is_kept_while_it_is_off(self):
        self.assertFalse(iga.add_visit(self.folder, "https://a.org/", "A", 1, remember=False))
        self.assertFalse(os.path.exists(iga.history_path(self.folder)))
        self.assertFalse(iga.DEFAULTS["iga_history"])

    def test_newest_first_bounded_private_and_clearable(self):
        for i in range(7):
            iga.add_visit(self.folder, f"https://a.org/{i}", f"P{i}", i, remember=True, most=5)
        iga.add_visit(self.folder, "https://a.org/3", "P3", 9, remember=True, most=5)
        urls = [h["url"] for h in iga.load_history(self.folder)]
        self.assertEqual(urls, ["https://a.org/3", "https://a.org/6", "https://a.org/5", "https://a.org/4",
                                "https://a.org/2"])
        self.assertEqual(mode(iga.history_path(self.folder)), 0o600)
        self.assertFalse(iga.add_visit(self.folder, iga.START, "Start", 1, remember=True))
        iga.clear_history(self.folder)
        self.assertEqual(iga.load_history(self.folder), [])
        iga.clear_history(self.folder)                      # clearing twice is fine
        self.assertEqual(iga.HISTORY_MOST, 500)


class DownloadTests(unittest.TestCase):
    def test_a_safe_name(self):
        none = lambda p: False  # noqa: E731
        self.assertEqual(iga.safe_name("report.pdf", "/d", none), "report.pdf")
        self.assertEqual(iga.safe_name("../../etc/passwd", "/d", none), "passwd")
        self.assertEqual(iga.safe_name("a\\b\\c.txt", "/d", none), "c.txt")
        self.assertEqual(iga.safe_name(".bashrc", "/d", none), "bashrc")
        self.assertEqual(iga.safe_name("", "/d", none), "download")
        self.assertEqual(iga.safe_name("..", "/d", none), "download")
        self.assertEqual(iga.safe_name("we\x00ird\n.txt", "/d", none), "weird.txt")

    def test_a_counter_when_the_file_is_there(self):
        there = {"/d/a.zip", "/d/a (2).zip", "/d/notes"}
        self.assertEqual(iga.safe_name("a.zip", "/d", there.__contains__), "a (3).zip")
        self.assertEqual(iga.safe_name("notes", "/d", there.__contains__), "notes (2)")

    def test_the_folder_and_the_lines(self):
        self.assertEqual(iga.downloads_folder("/home/u"), "/home/u/Downloads")
        self.assertEqual(iga.download_line("a.zip", 2_000_000, 4_000_000, "going"), "a.zip: 2.0 MB of 4.0 MB")
        self.assertEqual(iga.download_line("a.zip", 4_000_000, 4_000_000, "done"), "a.zip: saved (4.0 MB)")
        self.assertEqual(iga.download_line("a.zip", 0, 0, "failed"), "a.zip: failed")


class OwnPagesTests(unittest.TestCase):
    def test_the_start_page_lists_the_bookmarks_escaped(self):
        page = iga.start_page([{"title": "<script>alert(1)</script>", "url": "https://a.org/?x=\"><b>"},
                               {"title": "Bad", "url": "javascript:alert(1)"}], style.TOKENS["dark"])
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("href=\"https://a.org/?x=&quot;&gt;&lt;b&gt;\"", page)
        self.assertNotIn("javascript:", page)
        self.assertIn("loads only what you ask for", page)
        self.assertIn("part of the uConsole Ops app", page)

    def test_an_empty_start_page_says_how_to_add_one(self):
        self.assertIn("No bookmarks yet", iga.start_page([], style.TOKENS["light"]))

    def test_the_app_pages_take_the_apps_colours(self):
        for mode in ("dark", "light"):
            t = style.TOKENS[mode]
            page = iga.start_page([], t)
            for key in ("bg", "text", "accent", "muted"):
                self.assertIn(t[key], page, (mode, key))
        self.assertIn("background:#123456", iga.notice_page("x", "y", {"bg": "#123456"}))

    def test_odd_colours_cannot_break_out_of_the_style(self):
        c = iga.page_colours({"bg": "red;}</style><script>", "text": "#fff"})
        self.assertEqual(c["bg"], "#0f1114")
        self.assertEqual(c["text"], "#e6e8eb")

    def test_a_notice_is_escaped(self):
        page = iga.notice_page("Not opened", iga.NOT_ADDRESS, style.TOKENS["dark"], address="<img src=x>")
        self.assertIn("That is not an address.", page)
        self.assertIn("&lt;img src=x&gt;", page)
        self.assertNotIn("<img", page)

    def test_load_errors_in_plain_words(self):
        self.assertIn("could not be found", iga.load_error_text("dns"))
        self.assertIn("could not be loaded", iga.load_error_text("other"))


if __name__ == "__main__":
    unittest.main()

