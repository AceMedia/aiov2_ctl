import unittest

from ops_gui import dashboard as d


class ConfigTests(unittest.TestCase):
    def test_default_is_complete(self):
        c = d.default()
        self.assertEqual(c["order"], list(d.WIDGETS))
        self.assertEqual((c["hidden"], c["rails_hidden"], c["status_hidden"]), ([], [], []))

    def test_normalize_repairs_anything(self):
        for junk in (None, "x", 5, [], {"order": "nope", "hidden": 3, "rails_hidden": {}}):
            self.assertEqual(d.normalize(junk), d.default())
        c = d.normalize({"order": ["system", "bogus", "system", "battery"], "hidden": ["bogus", "rails", "rails"],
                         "rails_hidden": ["GPS", "XYZ"]})
        self.assertEqual(c["order"][:2], ["system", "battery"])
        self.assertEqual(sorted(c["order"]), sorted(d.WIDGETS))      # every card exactly once
        self.assertEqual(c["hidden"], ["rails"])
        self.assertEqual(c["rails_hidden"], ["GPS"])

    def test_new_cards_are_appended_for_old_settings(self):
        c = d.normalize({"order": ["battery", "rails", "radios"]})
        self.assertEqual(c["order"], ["battery", "rails", "radios", "companions", "system",
                                      "gps", "lora", "sdr", "wifi"])

    def test_move(self):
        c = d.move(d.default(), "wifi", -1)
        self.assertEqual(c["order"][-2:], ["wifi", "sdr"])
        self.assertEqual(d.move(d.default(), "battery", -1), d.default())          # already first
        self.assertEqual(d.move(d.default(), "wifi", 1), d.default())              # already last
        self.assertEqual(d.move(d.default(), "nope", 1), d.default())

    def test_show_and_hide(self):
        c = d.set_shown(d.default(), "radios", False)
        self.assertFalse(d.is_shown(c, "radios"))
        self.assertTrue(d.is_shown(d.set_shown(c, "radios", True), "radios"))
        self.assertEqual(d.set_shown(c, "radios", False)["hidden"], ["radios"])    # not twice
        self.assertEqual(d.set_shown(d.default(), "nope", False), d.default())

    def test_rails(self):
        c = d.set_rail_shown(d.default(), "LORA", False)
        self.assertEqual(d.shown_rails(c, {"GPS", "LORA", "SDR"}), ["GPS", "SDR"])
        self.assertEqual(d.shown_rails(d.default(), {"SDR", "GPS"}), ["GPS", "SDR"])   # usual order
        self.assertEqual(d.shown_rails(d.default(), set()), [])


class StatusBarTests(unittest.TestCase):
    def test_items_can_be_hidden(self):
        c = d.set_status_shown(d.default(), "ip", False)
        self.assertFalse(d.status_shown(c, "ip"))
        self.assertTrue(d.status_shown(c, "cpu"))
        self.assertEqual(d.set_status_shown(c, "ip", False)["status_hidden"], ["ip"])     # not twice
        self.assertTrue(d.status_shown(d.set_status_shown(c, "ip", True), "ip"))
        self.assertEqual(d.set_status_shown(d.default(), "nope", False), d.default())

    def test_bad_saved_values_are_repaired(self):
        self.assertEqual(d.normalize({"status_hidden": ["cpu", "bogus", "cpu"]})["status_hidden"], ["cpu"])
        self.assertEqual(d.normalize({"status_hidden": 5})["status_hidden"], [])

    def test_every_item_is_one_the_bar_can_show(self):
        from ops_gui import statusbar
        stats = {"cpu": 1.0, "temp": 50.0, "mem": (1, 2, 50.0), "load": 0.1, "disk": 10.0, "uptime": 99.0,
                 "ip": "1.2.3.4", "throttled": 0x4}
        keys = {k for k, _, _ in statusbar.chip_items(stats)}
        self.assertEqual(keys | {"battery"}, set(d.STATUS_ITEMS))
        self.assertEqual(len(statusbar.chips(stats, hidden=("cpu", "ip"))), len(keys) - 2)


class PlanTests(unittest.TestCase):
    ALL = {n: True for n in d.WIDGETS}

    def test_default_plan(self):
        self.assertEqual(d.plan(d.default(), self.ALL),
                         [("full", "battery"), ("full", "rails"),
                          ("grid", ["companions", "radios", "system", "gps", "lora", "sdr", "wifi"])])

    def test_empty_cards_are_left_out(self):
        content = dict(self.ALL, companions=False)
        self.assertEqual(d.plan(d.default(), content)[-1],
                         ("grid", ["radios", "system", "gps", "lora", "sdr", "wifi"]))

    def test_hidden_cards_are_left_out(self):
        c = d.set_shown(d.set_shown(d.default(), "battery", False), "system", False)
        self.assertEqual(d.plan(c, self.ALL),
                         [("full", "rails"), ("grid", ["companions", "radios", "gps", "lora", "sdr", "wifi"])])

    def test_order_splits_the_grid_around_a_wide_card(self):
        c = {"order": ["radios", "battery", "system", "rails", "companions"]}
        self.assertEqual(d.plan(c, self.ALL),
                         [("grid", ["radios"]), ("full", "battery"), ("grid", ["system"]),
                          ("full", "rails"), ("grid", ["companions", "gps", "lora", "sdr", "wifi"])])

    def test_everything_hidden_is_an_empty_plan(self):
        c = {"hidden": list(d.WIDGETS)}
        self.assertEqual(d.plan(c, self.ALL), [])

    def test_missing_content_info_means_show(self):
        self.assertEqual(len(d.plan(d.default(), {})), 3)

    def test_antenna_cards_follow_their_content(self):
        content = dict(self.ALL, gps=False, lora=False, sdr=True, wifi=False)
        self.assertEqual(d.plan(d.default(), content)[-1][1][-1], "sdr")
        self.assertNotIn("gps", d.plan(d.default(), content)[-1][1])


if __name__ == "__main__":
    unittest.main()
