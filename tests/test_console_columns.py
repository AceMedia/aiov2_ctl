import unittest

from ops_agent import columns

try:
    from rich.text import Text
except ImportError:                                   # the console's own dependency; skip where it is absent
    Text = None


@unittest.skipIf(Text is None, "rich is not installed here")
class TwoColumnTests(unittest.TestCase):
    """The console's two columns line up whatever colour tags or long lines are in them."""

    def lines(self, left, right, width=20, **kw):
        return columns.two_columns(left, right, width, Text, **kw).plain.split("\n")

    def test_the_right_column_starts_in_the_same_place_on_every_line(self):
        rows = self.lines(["[bold cyan]Activity[/bold cyan] [dim]ch0[/dim]", "plain", "[dim]No traffic on ch0 yet[/dim]"],
                          ["Radar", "7a58", "SMY1"])
        self.assertEqual([r.index(w) for r, w in zip(rows, ("Radar", "7a58", "SMY1"))], [23, 23, 23])

    def test_a_long_left_line_is_cut_and_does_not_run_into_the_right(self):
        rows = self.lines(["[dim]Selected ch0 (waiting for traffic) and more and more[/dim]"], ["7a58 95%"])
        self.assertEqual(rows[0].index("7a58"), 23)
        self.assertLessEqual(len(rows[0][:20].rstrip()), 20)

    def test_uneven_columns_are_filled_out(self):
        rows = self.lines(["one"], ["a", "b", "c"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2].index("c"), 23)

    def test_an_empty_right_line_adds_nothing(self):
        self.assertEqual(self.lines(["left"], [""]), ["left" + " " * 16])

    def test_the_right_column_is_cut_to_its_width_too(self):
        rows = self.lines(["x"], ["a very long line on the right hand side"], right_width=10)
        self.assertLessEqual(len(rows[0]), 20 + 3 + 10)

    def test_square_brackets_that_are_text_stay_text(self):
        rows = self.lines([r"\[ok] ready"], ["r"])
        self.assertTrue(rows[0].startswith("[ok] ready"))


class AgeWordsTests(unittest.TestCase):
    def test_long_ages_are_days_not_thousands_of_hours(self):
        self.assertEqual(columns.age_words(7422 * 3600), "309d")
        self.assertEqual(columns.age_words(47 * 3600), "47h")
        self.assertEqual(columns.age_words(90), "1m")
        self.assertEqual(columns.age_words(5), "5s")
        self.assertIsNone(columns.age_words(None))


if __name__ == "__main__":
    unittest.main()
