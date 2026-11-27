"""
Tests for tools/logo.py: the script draws the same logo and divider the wiki carries.

The pictures themselves are kept in the wiki, not in this repository, so the drawing is compared with the
SHA-256 of the pixels that were published. Change the logo on purpose and these numbers change with it.

Pillow draws in memory, so this needs no display. It is skipped where Pillow is not installed.

    python3 -m unittest discover -s tests
"""
import hashlib
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

    PUBLISHED = {
        "logo": ((1328, 252), "67a84184b0eeab5adbffa29a92dcf1093570436f9ad4f0598694eecbb8fcccb4"),
        "full": ((1328, 336), "b0c3725189a255ae17d3cd1db4edf87c446a6c3226fa75a3061113b1f29d4bc2"),
        "divider": ((1280, 64), "9f99c6882389fed22926d916cb874e312a74935c1a68d905571576bb87c64b55"),
    }

    def same_as_wiki(self, image, which):
        size, pixels = self.PUBLISHED[which]
        self.assertEqual(image.size, size)
        self.assertEqual(hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest(), pixels)
        self.assertTrue(self.logo.NAMES[which].endswith(".webp"))

    def test_font_and_its_licence_are_in_the_repository(self):
        self.assertTrue(os.path.isfile(self.logo.FONT))
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(self.logo.FONT), "OFL.txt")))

    def test_the_pictures_are_not_written_into_the_repository_by_default(self):
        self.assertFalse(os.path.isabs(self.logo.OUT))
        self.assertNotIn("docs", self.logo.OUT)

    def test_logo_matches_the_wiki(self):
        self.same_as_wiki(self.logo.logo(), "logo")

    def test_full_logo_matches_the_wiki(self):
        self.same_as_wiki(self.logo.logo(sub=True), "full")

    def test_divider_matches_the_wiki(self):
        self.same_as_wiki(self.logo.divider(), "divider")


if __name__ == "__main__":
    unittest.main()
