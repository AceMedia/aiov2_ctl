"""
Tests for tools/logo.py: the script draws the same logo and divider the wiki carries.

Pillow draws in memory, so this needs no display. It is skipped where Pillow is not installed.

    python3 -m unittest discover -s tests
"""
import importlib.util
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from PIL import Image
except ImportError:                                     # pragma: no cover
    Image = None


@unittest.skipIf(Image is None, "Pillow is not installed")
class Logo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("logo", os.path.join(REPO, "tools", "logo.py"))
        cls.logo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.logo)

    def same_as_wiki(self, image, name):
        with Image.open(os.path.join(REPO, "docs", "wiki", "img", "app", name)) as kept:
            kept = kept.convert("RGBA")
            self.assertEqual(image.size, kept.size)
            self.assertEqual(image.convert("RGBA").tobytes(), kept.tobytes())

    def test_font_and_its_licence_are_in_the_repository(self):
        self.assertTrue(os.path.isfile(self.logo.FONT))
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(self.logo.FONT), "OFL.txt")))

    def test_default_folder_is_the_wiki_pictures(self):
        self.assertEqual(os.path.normpath(self.logo.OUT),
                         os.path.normpath(os.path.join(REPO, "docs", "wiki", "img", "app")))

    def test_logo_matches_the_wiki(self):
        self.same_as_wiki(self.logo.logo(), self.logo.NAMES["logo"])

    def test_full_logo_matches_the_wiki(self):
        self.same_as_wiki(self.logo.logo(sub=True), self.logo.NAMES["full"])

    def test_divider_matches_the_wiki(self):
        self.same_as_wiki(self.logo.divider(), self.logo.NAMES["divider"])


if __name__ == "__main__":
    unittest.main()
