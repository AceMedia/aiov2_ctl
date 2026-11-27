"""
The local wiki: the installer's two features (the Wiki tab's switch, and the pictures' download) and the pure
parts of the app's Wiki tab. No network, no display, no root.

    python3 -m unittest discover -s tests
"""
import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402
from ops_gui import data, wiki  # noqa: E402

FACTS = {"aio_board": True, "internal_card": True, "flipper": True, "desktop": True}


def state(**changes):
    base = {"user": "u", "home": "/home/u", "app_source": "/home/u/aiov2_ctl", "old_checkout": False,
            "installed": set(), "unavailable": set(), "sudoers_listening": False, "present": set(),
            "wiki_pictures": {"count": 2, "bytes": 2_500_000}, "remote": "https://github.com/someone/aiov2_ctl.git"}
    base.update(changes)
    return base


def entry(data_bytes):
    return {"bytes": len(data_bytes), "sha256": hashlib.sha256(data_bytes).hexdigest()}


class FeatureTests(unittest.TestCase):
    def test_the_wiki_needs_the_app_and_its_pictures_need_the_wiki(self):
        self.assertEqual(a.resolve_features(["wikipics"]), ["core", "app", "wiki", "wikipics"])
        self.assertTrue(a.feature_row("wiki")["default"])
        self.assertFalse(a.feature_row("wikipics")["default"], "a download is something you ask for")
        self.assertEqual(a.feature_row("wikipics")["apt"], ("qt6-image-formats-plugins",))

    def test_switching_the_wiki_on_is_one_step_done_as_the_user(self):
        plan = [s for s in a.install_plan(["wiki"], FACTS, state()) if s["feature"] == "wiki"]
        self.assertEqual([(s["id"], s["kind"], s["root"]) for s in plan], [("wiki.on", "wiki-on", False)])
        self.assertEqual(plan[0]["path"], "/home/u/.local/share/aiov2_ctl/wiki")
        self.assertIn("read offline", plan[0]["detail"])

    def test_the_download_step_says_how_much_from_where_and_that_nothing_is_sent(self):
        plan = [s for s in a.install_plan(["wikipics"], FACTS, state()) if s["feature"] == "wikipics"]
        self.assertEqual([s["kind"] for s in plan], ["apt", "wiki-pictures"])
        step = plan[1]
        self.assertFalse(step["root"])
        self.assertEqual(step["url"], "https://raw.githubusercontent.com/wiki/someone/aiov2_ctl/")
        for words in ("2 pictures", "2.5 MB", step["url"], "checked against the list", "Nothing about you"):
            self.assertIn(words, step["detail"])

    def test_with_no_checkout_or_no_list_the_wiki_is_not_installed_and_nothing_fails(self):
        for key, changes in (("wiki", {"app_source": None}), ("wikipics", {"app_source": None}),
                             ("wikipics", {"wiki_pictures": {"count": 0, "bytes": 0}})):
            mine = [s for s in a.install_plan([key], FACTS, state(**changes)) if s["feature"] == key
                    and s["kind"] == "note"]
            self.assertTrue(mine and mine[-1].get("skips"), (key, changes))
            self.assertEqual(a._act_note(mine[-1], {})[0], "skipped")

    def test_every_new_step_has_something_that_carries_it_out(self):
        for step in a.install_plan(["wikipics"], FACTS, state()):
            self.assertIsNotNone(a.step_action(step), step["id"])
        for step in a.uninstall_plan(["wikipics", "wiki"], state()):
            self.assertIn(step["kind"], a.UNINSTALL_ACTIONS, step["id"])

    def test_removing_takes_the_switch_and_the_pictures_and_nothing_else(self):
        plan = a.uninstall_plan(["wikipics", "wiki"], state())
        self.assertEqual([s["id"] for s in plan], ["un.wikipics.pictures", "un.wiki.off"])
        self.assertEqual(plan[0]["path"], "/home/u/.local/share/aiov2_ctl/wiki/img")
        self.assertEqual(plan[1]["paths"], ("/home/u/.local/share/aiov2_ctl/wiki/enabled",))
        self.assertIn("stays installed", plan[0]["detail"])

    def test_the_pictures_step_refuses_any_other_folder(self):
        calls = []

        def run(cmd, cwd=None, timeout=None):
            calls.append(cmd)
            return 0, "", ""
        status, words = a._un_wiki_pictures({"path": "/home/u"}, {"user": "u", "home": "/home/u"}, run)
        self.assertEqual(status, "failed")
        self.assertEqual(calls, [])

    def test_the_switch_and_the_download_run_as_the_user(self):
        seen = []

        def run(cmd, cwd=None, timeout=None):
            seen.append(cmd)
            return 0, "2 downloaded, 0 already here, 0 failed.\n", ""
        with mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.object(a.os.path, "isfile", return_value=False):
            status, _ = a._act_wiki_on({"path": "/home/u/.local/share/aiov2_ctl/wiki"}, {"user": "u"}, run)
        self.assertEqual(status, "done")
        self.assertEqual(seen[0][:5], ["sudo", "-u", "u", "-H", "--"])
        step = {"path": "/home/u/w", "source": "/home/u/aiov2_ctl", "url": "https://x/"}
        with mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.object(a, "load_wiki_pictures", return_value={"img/a.webp": {"bytes": 1, "sha256": "0" * 64}}):
            status, words = a._act_wiki_pictures(step, {"user": "u"}, run)
        self.assertEqual((status, words), ("done", "2 downloaded, 0 already here, 0 failed."))
        self.assertEqual(seen[1][:5], ["sudo", "-u", "u", "-H", "--"])
        self.assertEqual(seen[1][-5:], ["--wiki-pictures", "fetch", "/home/u/aiov2_ctl", "/home/u/w", "https://x/"])
        self.assertEqual(a._act_wiki_pictures(step, {"user": None}, run)[0], "failed")


class PictureListTests(unittest.TestCase):
    def test_only_sound_entries_are_kept(self):
        good = {"img/app/a.webp": {"bytes": 10, "sha256": "a" * 64}}
        bad = {"img/../../etc/passwd.webp": {"bytes": 10, "sha256": "a" * 64},
               "/etc/x.webp": {"bytes": 10, "sha256": "a" * 64},
               "img/a.png": {"bytes": 10, "sha256": "a" * 64},
               "img/b.webp": {"bytes": 0, "sha256": "a" * 64},
               "img/c.webp": {"bytes": a.WIKI_PICTURE_LIMIT + 1, "sha256": "a" * 64},
               "img/d.webp": {"bytes": 10, "sha256": "nope"},
               "img/e.webp": {"bytes": True, "sha256": "a" * 64},
               "img/f.webp": "x", 7: {}}
        mixed = dict(good)
        mixed.update(bad)
        self.assertEqual(a.good_wiki_pictures(mixed), good)
        self.assertEqual(a.good_wiki_pictures(["not", "a", "dict"]), {})

    def test_the_real_list_is_sound_and_every_picture_a_page_links_is_on_it(self):
        pictures = a.load_wiki_pictures(REPO)
        self.assertGreater(len(pictures), 40)
        self.assertLess(sum(p["bytes"] for p in pictures.values()), 2_000_000, "the pictures are meant to be small")
        self.assertEqual(a.load_wiki_pictures("/nonexistent"), {})

    def test_the_address_follows_the_repository_this_was_installed_from(self):
        for url in ("https://github.com/Some-One/aiov2_ctl.git", "git@github.com:Some-One/aiov2_ctl.git",
                    "https://github.com/Some-One/aiov2_ctl/", "ssh://git@github.com/Some-One/aiov2_ctl"):
            self.assertEqual(a.repo_slug(url), "Some-One/aiov2_ctl", url)
        for url in ("", None, "/home/u/origin.git", "https://example.org/a/b.git"):
            self.assertIsNone(a.repo_slug(url), url)
        self.assertEqual(a.wiki_pictures_url(None), f"https://raw.githubusercontent.com/wiki/{a.PROJECT_REPO}/")

    def test_a_download_is_checked_before_it_is_kept(self):
        one, two, three = b"first picture", b"second picture", b"third"
        pictures = {"img/a.webp": entry(one), "img/sub/b.webp": entry(two), "img/c.webp": entry(three)}
        asked = []

        def get(url):
            asked.append(url)
            if url.endswith("b.webp"):
                return b"something else entirely"
            if url.endswith("c.webp"):
                raise OSError("no route to host")
            return one
        with tempfile.TemporaryDirectory() as d:
            fetched, already, failed = a.fetch_wiki_pictures(pictures, d, "https://w/", get)
            self.assertEqual((fetched, already), (["img/a.webp"], []))
            self.assertEqual([p for p, _ in failed], ["img/c.webp", "img/sub/b.webp"])
            self.assertIn("not the listed picture", dict(failed)["img/sub/b.webp"])
            self.assertIn("no route", dict(failed)["img/c.webp"])
            self.assertEqual(sorted(os.listdir(os.path.join(d, "img"))), ["a.webp"], "nothing wrong is left behind")
            self.assertEqual(a.wiki_pictures_missing(pictures, d), ["img/c.webp", "img/sub/b.webp"])
            asked.clear()
            fetched, already, failed = a.fetch_wiki_pictures({"img/a.webp": entry(one)}, d, "https://w/", get)
            self.assertEqual((fetched, already, failed, asked), ([], ["img/a.webp"], [], []), "not fetched twice")

    def test_the_download_command_is_not_for_root_and_takes_only_safe_addresses(self):
        with mock.patch.object(a.os, "geteuid", return_value=0), mock.patch("builtins.print"):
            self.assertEqual(a.wiki_pictures_command(["fetch", REPO, "/tmp/x", "https://w/"]), 1)
        with mock.patch.object(a.os, "geteuid", return_value=1000), mock.patch("builtins.print"):
            self.assertEqual(a.wiki_pictures_command(["fetch", REPO, "/tmp/x", "http://w/"]), 2)
            self.assertEqual(a.wiki_pictures_command(["fetch"]), 2)


class WikiTabTests(unittest.TestCase):
    NAMES = ["CLI‐Usage.md", "Home.md", "OS-Compatibility-&-Requirements.md", "Ops-App.md", "_Sidebar.md",
             "pictures.json"]

    def test_the_tab_comes_after_help_and_only_when_it_is_on(self):
        self.assertEqual(data.tab_names({}, False)[-1], "Help")
        self.assertEqual(data.tab_names({}, False, wiki=True)[-2:], ["Help", "Wiki"])
        self.assertIn("Wiki", data.TAB_ICONS)

    def test_it_is_on_when_the_installer_switched_it_on_and_the_pages_are_here(self):
        have = {"/u/enabled", "/p/Home.md"}
        self.assertTrue(wiki.enabled("/p", "/u", isfile=lambda p: p in have))
        self.assertFalse(wiki.enabled("/p", "/u", isfile=lambda p: p == "/p/Home.md"))
        self.assertFalse(wiki.enabled("/p", "/u", isfile=lambda p: p == "/u/enabled"))
        self.assertTrue(wiki.user_folder("/home/u").endswith("/.local/share/aiov2_ctl/wiki"))
        self.assertEqual(wiki.user_folder("/home/u"), a.wiki_home("/home/u"), "the app and the installer agree")

    def test_a_link_finds_its_page_whatever_hyphen_it_was_written_with(self):
        self.assertEqual(wiki.page_file("CLI-Usage", self.NAMES), "CLI‐Usage.md")
        self.assertEqual(wiki.page_file("cli-usage", self.NAMES), "CLI‐Usage.md")
        self.assertEqual(wiki.page_file("OS-Compatibility-&-Requirements", self.NAMES),
                         "OS-Compatibility-&-Requirements.md")
        self.assertIsNone(wiki.page_file("Nowhere", self.NAMES))
        self.assertIsNone(wiki.page_file("pictures", self.NAMES), "only pages are pages")
        self.assertEqual(wiki.title("CLI‐Usage"), "CLI Usage")

    def test_links_are_told_apart(self):
        self.assertEqual(wiki.link_kind("https://example.org/x"), ("web", "https://example.org/x"))
        self.assertEqual(wiki.link_kind("Ops-App"), ("page", "Ops-App", ""))
        self.assertEqual(wiki.link_kind("Troubleshooting#sdr-conflicts"), ("page", "Troubleshooting", "sdr-conflicts"))
        self.assertEqual(wiki.link_kind("#keys"), ("anchor", "keys"))
        self.assertEqual(wiki.link_kind("img/app/x.webp"), ("picture", "img/app/x.webp"))

    def test_a_page_reads_the_same_without_its_pictures(self):
        text = ("Intro.\n\n[![The plan](img/install/plan.webp)](img/install/plan.webp)\n\n![](img/app/divider.webp)\n\n"
                '<img style="width: 300px" src="https://example.org/remote.png" />\n\n![Badge](https://example.org/b.svg)\n')
        without = wiki.prepare(text, lambda p: False)
        self.assertIn("*[Picture: The plan]*", without)
        self.assertNotIn("img/", without)
        self.assertNotIn("example.org", without, "a picture on another site is never fetched")
        self.assertEqual(without.count("*[A picture on the website]*"), 2)
        with_them = wiki.prepare(text, lambda p: True)
        self.assertIn("![The plan](img/install/plan.webp)", with_them)
        self.assertNotIn("](img/install/plan.webp)](", with_them, "the link to itself is dropped")
        self.assertIn("![](img/app/divider.webp)", with_them)
        self.assertTrue(wiki.has_pictures(text))
        self.assertFalse(wiki.has_pictures("No pictures here. ![x](https://example.org/y.png)"))

    def test_a_page_gets_its_name_as_a_heading_only_when_it_has_none(self):
        self.assertEqual(wiki.with_title("# Installation\n\nText.", "Installation"), "# Installation\n\nText.")
        self.assertEqual(wiki.with_title("\n\n# Ops App\n", "Ops-App"), "\n\n# Ops App\n")
        self.assertTrue(wiki.with_title("Plain opening line.\n\n## Status\n", "CLI\u2010Usage")
                        .startswith("# CLI Usage\n\nPlain opening line."))
        self.assertEqual(wiki.with_title("", "Home"), "# Home\n")

    def test_the_sidebar_becomes_rows_for_a_list_with_headings_that_are_not_pages(self):
        text = ("**Setting up**\n\n- [Home](Home)\n- [OS Compatibility](OS-Compatibility-&-Requirements)\n\n"
                "**Using it**\n\n- [CLI Usage](CLI-Usage)\n- [The project](https://example.org)\nplain words\n")
        rows = wiki.sidebar_entries(text)
        self.assertEqual(rows, [("heading", "Setting up"), ("page", "Home", "Home"),
                                ("page", "OS Compatibility", "OS-Compatibility-&-Requirements"),
                                ("heading", "Using it"), ("page", "CLI Usage", "CLI-Usage")])
        self.assertEqual(wiki.row_of(rows, "CLI\u2010Usage"), 4, "the list follows a link, whatever the hyphen")
        self.assertEqual(wiki.row_of(rows, "home"), 1)
        self.assertEqual(wiki.row_of(rows, "Nowhere"), -1)
        self.assertEqual(wiki.sidebar_entries(""), [])

    def test_the_sidebar_is_a_tree_with_pages_under_their_headings(self):
        rows = [("page", "Start", "Start"), ("heading", "Setting up"), ("page", "Home", "Home"),
                ("page", "Installation", "Installation"), ("heading", "Help"), ("page", "Health Check", "Health-Check")]
        self.assertEqual(wiki.sidebar_tree(rows),
                         [(None, [("Start", "Start")]),
                          ("Setting up", [("Home", "Home"), ("Installation", "Installation")]),
                          ("Help", [("Health Check", "Health-Check")])])
        self.assertEqual(wiki.sidebar_tree([]), [])
        self.assertGreaterEqual(wiki.INDENT, 16, "a page sits visibly inside its heading")

    def test_up_and_down_never_stop_on_a_heading(self):
        kinds = ["heading", "page", "page", "heading", "page", "heading"]
        self.assertEqual(wiki.step_over(kinds, 2, 3), 4, "down from the last page of a section: the next section's first")
        self.assertEqual(wiki.step_over(kinds, 4, 3), 2, "up from the first page of a section: the one above's last")
        self.assertEqual(wiki.step_over(kinds, 1, 0), 1, "up from the first page of all: stays where it was")
        self.assertEqual(wiki.step_over(kinds, 4, 5), 4, "down from the last page of all: stays where it was")
        self.assertEqual(wiki.step_over(kinds, None, 0), 1, "nothing chosen yet: the first page")
        self.assertEqual(wiki.step_over(kinds, 1, 2), 2, "a page is a place to stop")

    def test_blocks_of_a_page_have_air_between_them(self):
        para, item, code = wiki.block_spacing(0, False, False), wiki.block_spacing(0, True, False), \
            wiki.block_spacing(0, False, True)
        self.assertGreater(para[1], item[1], "list rows sit closer together than paragraphs")
        self.assertGreater(item[1], 0)
        self.assertEqual(code, (0, 0, 100), "code keeps its own line height so columns line up")
        self.assertGreater(para[2], 100)
        h1, h2, h3 = (wiki.block_spacing(n, False, False) for n in (1, 2, 3))
        for h in (h1, h2, h3):
            self.assertGreater(h[0], h[1], "a heading stands clear of what is above it")
        self.assertEqual(wiki.block_spacing(1, False, False, first=True)[0], 0, "no gap above the page's title")

    def test_a_row_is_big_enough_for_a_trackball_and_the_hints_name_the_real_controls(self):
        self.assertGreaterEqual(wiki.ROW_HEIGHT, 40)
        for words in ("Up and down", "A or Enter"):
            self.assertIn(words, wiki.HINT_LIST)
        for words in ("scroll", "B, Esc or the right button"):
            self.assertIn(words, wiki.HINT_PAGE)

    def test_the_real_sidebar_lists_every_page(self):
        folder = wiki.pages_folder()
        with open(os.path.join(folder, wiki.SIDEBAR), encoding="utf-8") as f:
            rows = wiki.sidebar_entries(f.read())
        pages = [n[:-3] for n in os.listdir(folder) if n.endswith(".md") and not n.startswith("_")]
        self.assertEqual(sorted(wiki.plain(p) for p in pages), sorted(wiki.plain(r[2]) for r in rows if r[0] == "page"))
        self.assertGreaterEqual(sum(1 for r in rows if r[0] == "heading"), 3)

    def test_a_wide_picture_is_made_to_fit_and_a_small_one_is_left(self):
        self.assertEqual(wiki.fitted(1280, 696, 640), (640, 348))
        self.assertEqual(wiki.fitted(400, 100, 640), (400, 100))
        self.assertEqual(wiki.fitted(0, 0, 640), (0, 0))

    def test_every_page_in_the_real_wiki_can_be_reached_from_the_sidebar_and_prepared(self):
        folder = wiki.pages_folder()
        names = sorted(os.listdir(folder))
        with open(os.path.join(folder, wiki.SIDEBAR), encoding="utf-8") as f:
            sidebar = f.read()
        import re
        targets = re.findall(r"\]\(([^)]+)\)", sidebar)
        self.assertGreater(len(targets), 15)
        for target in targets:
            kind = wiki.link_kind(target)
            self.assertEqual(kind[0], "page", target)
            self.assertIsNotNone(wiki.page_file(kind[1], names), target)
        for name in names:
            if name.endswith(".md"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    self.assertNotIn("](img/", wiki.prepare(f.read(), lambda p: False), name)


if __name__ == "__main__":
    unittest.main()

