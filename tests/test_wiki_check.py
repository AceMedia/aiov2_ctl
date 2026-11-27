"""
Tests for tools/wiki-check.py, the link checker for the wiki source in docs/wiki/.

The last class runs the checker on the real docs/wiki/, so a broken link there fails the suite.
Nothing here uses the network or a display:

    python3 -m unittest discover -s tests
"""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = os.path.join(REPO, "docs", "wiki")

_spec = importlib.util.spec_from_file_location("wiki_check", os.path.join(REPO, "tools", "wiki-check.py"))
wc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wc)

SIDEBAR = "- [Home](Home)\n- [Other](Other)\n"


def wiki(**pages):
    """A small wiki: Home, Other and a sidebar naming both, with `pages` laid over them."""
    out = {"Home.md": "# Home\n", "Other.md": "# Other\n\n## A part\n", "_Sidebar.md": SIDEBAR}
    out.update({name + ".md": text for name, text in pages.items()})
    return out


class Names(unittest.TestCase):
    def test_unicode_hyphen_is_a_plain_hyphen(self):
        self.assertEqual(wc.page_name("CLI‐Usage.md"), "CLI-Usage")

    def test_other_names_are_kept(self):
        self.assertEqual(wc.page_name("OS-Compatibility-&-Requirements.md"), "OS-Compatibility-&-Requirements")


class Links(unittest.TestCase):
    def targets(self, text):
        return [t for _, t in wc.find_links(text)]

    def test_link_and_picture(self):
        self.assertEqual(self.targets("See [the page](Other) and ![pic](img/a.png)."), ["Other", "img/a.png"])

    def test_picture_inside_a_link_gives_both(self):
        self.assertEqual(self.targets("[![Home](img/a.png)](img/b.png)"), ["img/a.png", "img/b.png"])

    def test_title_after_the_target_is_dropped(self):
        self.assertEqual(self.targets('[x](Other "A title")'), ["Other"])

    def test_html_img_and_a(self):
        self.assertEqual(self.targets('<img alt="x" src="img/a.png" /> <a href="Other">o</a>'),
                         ["img/a.png", "Other"])

    def test_double_bracket_links(self):
        self.assertEqual(self.targets("[[Other]] and [[the words|Other Page]]"), ["Other", "Other-Page"])

    def test_links_in_code_are_not_links(self):
        text = "```\n[x](Nowhere)\n```\nand `[y](Nowhere)` but [z](Other)\n"
        self.assertEqual(wc.find_links(text), [(4, "Other")])

    def test_line_numbers(self):
        self.assertEqual(wc.find_links("a\n\n[x](Other)\n"), [(3, "Other")])


class Targets(unittest.TestCase):
    def test_other_sites_are_external(self):
        for t in ("https://example.org/a", "http://x", "mailto:a@b.c", "//cdn/x"):
            self.assertEqual(wc.split_target(t)[0], "external", t)

    def test_page(self):
        self.assertEqual(wc.split_target("Other"), ("page", "Other", None))

    def test_page_with_anchor(self):
        self.assertEqual(wc.split_target("Other#a-part"), ("page", "Other", "a-part"))

    def test_anchor_on_this_page(self):
        self.assertEqual(wc.split_target("#a-part"), ("page", "", "a-part"))

    def test_picture_is_a_file(self):
        self.assertEqual(wc.split_target("img/app/a.png"), ("file", "img/app/a.png", None))

    def test_percent_escapes_are_decoded(self):
        self.assertEqual(wc.split_target("img/a%20b.png")[1], "img/a b.png")

    def test_unicode_hyphen_in_a_link(self):
        self.assertEqual(wc.split_target("CLI‐Usage")[1], "CLI-Usage")


class Anchors(unittest.TestCase):
    def test_punctuation_goes_and_spaces_become_hyphens(self):
        self.assertEqual(wc.anchor_for("Wi-Fi / Bluetooth stability issues"), "wi-fi--bluetooth-stability-issues")
        self.assertEqual(wc.anchor_for("SDR conflicts (one app at a time)"), "sdr-conflicts-one-app-at-a-time")

    def test_code_in_a_heading_keeps_its_words(self):
        self.assertEqual(wc.anchor_for("The `--doctor` check"), "the---doctor-check")

    def test_repeated_heading_is_numbered(self):
        self.assertEqual(wc.heading_anchors("# One\n## Two\n## Two\n"), {"one", "two", "two-1"})

    def test_comment_in_a_code_block_is_not_a_heading(self):
        self.assertEqual(wc.heading_anchors("# Real\n```sh\n# not a heading\n```\n"), {"real"})


class Check(unittest.TestCase):
    def test_sound_wiki_has_no_problems(self):
        pages = wiki(Home="# Home\n[o](Other#a-part) ![p](img/a.png) [here](#home) [site](https://x.org/y)\n")
        self.assertEqual(wc.check_pages(pages, {"img/a.png"}), [])

    def test_missing_page(self):
        problems = wc.check_pages(wiki(Home="[x](Nowhere)\n"), set())
        self.assertEqual(problems, ["Home.md:1: no such page: Nowhere"])

    def test_missing_picture(self):
        problems = wc.check_pages(wiki(Home="\n![x](img/gone.png)\n"), {"img/a.png"})
        self.assertEqual(problems, ["Home.md:2: no such file: img/gone.png"])

    def test_picture_name_must_match_case(self):
        self.assertEqual(len(wc.check_pages(wiki(Home="![x](img/A.png)\n"), {"img/a.png"})), 1)

    def test_missing_heading(self):
        problems = wc.check_pages(wiki(Home="[x](Other#no-such-part)\n"), set())
        self.assertEqual(problems, ['Home.md:1: no heading "#no-such-part" on page Other'])

    def test_missing_heading_on_the_same_page(self):
        self.assertEqual(len(wc.check_pages(wiki(Home="# Home\n[x](#gone)\n"), set())), 1)

    def test_link_out_of_the_folder(self):
        problems = wc.check_pages(wiki(Home="![x](../secret.png)\n"), set())
        self.assertEqual(problems, ["Home.md:1: link leaves the wiki folder: ../secret.png"])

    def test_page_not_in_the_sidebar(self):
        pages = wiki()
        pages["Extra.md"] = "# Extra\n"
        self.assertEqual(wc.check_pages(pages, set()), ["_Sidebar.md: page not listed: Extra"])

    def test_footer_need_not_be_in_the_sidebar(self):
        pages = wiki()
        pages["_Footer.md"] = "the end\n"
        self.assertEqual(wc.check_pages(pages, set()), [])

    def test_no_sidebar(self):
        pages = wiki()
        del pages["_Sidebar.md"]
        self.assertEqual(wc.check_pages(pages, set()), ["_Sidebar.md is missing"])

    def test_unicode_hyphen_file_is_found_by_a_plain_link(self):
        pages = {"Home.md": "[cli](CLI-Usage)\n", "CLI‐Usage.md": "# CLI\n",
                 "_Sidebar.md": "[Home](Home) [CLI](CLI-Usage)\n"}
        self.assertEqual(wc.check_pages(pages, set()), [])

    def test_unused_files_are_named_but_are_not_problems(self):
        pages = wiki(Home="![x](img/a.png)\n")
        self.assertEqual(wc.unused_files(pages, {"img/a.png", "img/b.png"}), ["img/b.png"])
        self.assertEqual(wc.check_pages(pages, {"img/a.png", "img/b.png"}), [])


class OnDisk(unittest.TestCase):
    def make(self, home):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.mkdir(os.path.join(tmp.name, "img"))
        for name, text in (("Home.md", home), ("_Sidebar.md", "[Home](Home)\n"), ("img/a.png", "")):
            with open(os.path.join(tmp.name, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        return tmp.name

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = wc.main(list(args))
        return status, out.getvalue() + err.getvalue()

    def test_sound_folder_exits_zero(self):
        status, text = self.run_main(self.make("![x](img/a.png)\n"))
        self.assertEqual(status, 0)
        self.assertIn("nothing broken", text)

    def test_broken_folder_exits_one_and_names_the_fault(self):
        status, text = self.run_main(self.make("![x](img/gone.png)\n[y](Nowhere)\n"))
        self.assertEqual(status, 1)
        self.assertIn("BROKEN  Home.md:1: no such file: img/gone.png", text)
        self.assertIn("BROKEN  Home.md:2: no such page: Nowhere", text)

    def test_missing_folder_exits_two(self):
        status, _ = self.run_main(os.path.join(self.make(""), "nope"))
        self.assertEqual(status, 2)


class RealWiki(unittest.TestCase):
    """The wiki source this repository carries."""

    def test_it_is_there(self):
        self.assertTrue(os.path.isfile(os.path.join(WIKI, "Home.md")))
        self.assertTrue(os.path.isfile(os.path.join(WIKI, "_Sidebar.md")))

    def test_nothing_is_broken(self):
        self.assertEqual(wc.check_wiki(WIKI), [])

    def test_default_folder_is_docs_wiki(self):
        self.assertEqual(os.path.normpath(wc.DEFAULT_DIR), os.path.normpath(WIKI))

    def test_draft_notes_are_not_published(self):
        self.assertFalse(os.path.exists(os.path.join(WIKI, "DRAFT-NOTES.md")))

    def test_only_pages_and_pictures(self):
        pages, files = wc.read_wiki(WIKI)
        self.assertEqual([f for f in sorted(files) if not (f.startswith("img/") and f.endswith(".png"))], [])


if __name__ == "__main__":
    unittest.main()
