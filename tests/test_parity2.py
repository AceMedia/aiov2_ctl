import inspect
import unittest

from ops_gui import marauder_panel, net_panel, style


class ParityTwoTests(unittest.TestCase):
    def test_network_and_marauder_cards_have_tags(self):
        self.assertIn('"WIRED LINK"', inspect.getsource(net_panel))
        self.assertIn('"NEIGHBOURS"', inspect.getsource(net_panel))
        src = inspect.getsource(marauder_panel)
        self.assertIn('"NETWORKS HEARD"', src)
        self.assertIn('"LIVE OUTPUT"', src)
        self.assertNotIn("13pt; color: #9e9e9e", src.split("NETWORKS HEARD")[1][:200])

    def test_tables_have_a_dotted_rule_under_each_row(self):
        for skin in (None, {}):
            self.assertIn("QTableWidget::item { border-bottom: 1px dotted", style.stylesheet("dark", skin or None))


if __name__ == "__main__":
    unittest.main()
