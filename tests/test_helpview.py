import unittest

from ops_gui import helpview as h


class BitmapTests(unittest.TestCase):
    def test_every_bitmap_is_sixteen_by_sixteen(self):
        sets = {"dpad": h.DPAD, "disc": h.DISC, "trackball": h.TRACKBALL, "arrow": h.ARROW_RIGHT}
        sets.update({f"tab {k}": v for k, v in h.TAB_ICONS.items()})
        for name, rows in sets.items():
            self.assertEqual(len(rows), 16, name)
            self.assertTrue(all(len(r) == 16 for r in rows), name)

    def test_only_known_characters_are_used(self):
        for name, rows in dict(dpad=h.DPAD, disc=h.DISC, trackball=h.TRACKBALL, **{k: v for k, v in h.TAB_ICONS.items()}).items():
            self.assertTrue(set("".join(rows)) <= set(".#oudlrm"), name)

    def test_every_tab_icon_has_an_accent_pixel_and_unknown_names_fall_back(self):
        for name in h.TAB_ICONS:
            if name != "Bluetooth":                                        # the Bluetooth mark is ink only
                self.assertIn("o", "".join(h.tab_icon(name)), name)        # each has its one accent pixel colour
        self.assertEqual(h.tab_icon("Nope"), h.TAB_ICONS["Overview"])

    def test_dpad_variants_light_only_the_named_arms(self):
        lr = h.variant(h.DPAD, "lr")
        text = "".join(lr)
        self.assertNotIn("u", text)
        self.assertNotIn("d", text.replace("#", ""))
        self.assertIn("o", text)
        ud = "".join(h.variant(h.DPAD, "ud"))
        self.assertEqual(sum(c == "o" for c in ud), 24)
        self.assertEqual(sum(c == "o" for c in text), 32)

    def test_face_buttons_wear_their_letter(self):
        for letter in "ABXY":
            rows = h.face(letter)
            self.assertEqual(len(rows), 16)
            self.assertTrue(all(len(r) == 16 for r in rows))
            self.assertGreater(sum(r.count("#") for r in rows), sum(r.count("#") for r in h.DISC))
        self.assertNotEqual(h.face("A"), h.face("B"))
        self.assertEqual(h.face("x"), h.face("X"))

    def test_trackball_variants(self):
        right, middle = "".join(h.variant(h.TRACKBALL, "r")), "".join(h.variant(h.TRACKBALL, "m"))
        both = "".join(h.variant(h.TRACKBALL, "rm"))
        self.assertNotEqual(right, middle)
        self.assertEqual(right.count("o"), 8)
        self.assertEqual(middle.count("o"), 4)
        self.assertEqual(both.count("o"), 12)

    def test_arrows(self):
        right, left = h.arrow("right"), h.arrow("left")
        self.assertEqual([r[::-1] for r in right], left)
        down, up = h.arrow("down"), h.arrow("up")
        self.assertEqual(len(down), 16)
        self.assertEqual(up, down[::-1])
        self.assertEqual(sum(r.count("#") for r in right), sum(r.count("#") for r in down))

    def test_symbols_for_every_button_row(self):
        for kind, which, text in h.BUTTONS:
            rows = h.symbol(kind, which)
            self.assertEqual(len(rows), 16, (kind, which))
            self.assertLessEqual(len(text), 40)


class ContentTests(unittest.TestCase):
    def test_descriptions_are_short_enough_not_to_wrap(self):
        for keys, text in h.KEYS + h.RAILS + h.GENERAL_KEYS:
            self.assertLessEqual(len(text), 40, text)

    def test_the_everything_key_is_listed_and_is_not_a_gamepad_or_rail_letter(self):
        letters = [keys[0] for keys, _ in h.KEYS + h.RAILS if len(keys) == 1]
        self.assertIn("E", letters)
        self.assertEqual(len(letters), len(set(letters)))                    # no letter does two jobs
        self.assertNotIn("E", ("J", "K", "U", "I"))                          # what A B X Y send in keyboard mode
        self.assertTrue(any("Everything" in text for keys, text in h.KEYS if keys == ["E"]))

    def test_chip_width(self):
        self.assertEqual(h.chip_width("R"), 24)
        self.assertEqual(h.chip_width("Tab"), 36)
        self.assertEqual(h.chip_width("Shift"), 52)

    def test_the_gamepad_note_warns_about_the_letters(self):
        self.assertIn("j k u i", h.PAD_NOTE)

    def test_companion_rows(self):
        rows = h.companion_rows({"companions": [{"label": "Flipper Zero", "tty": "/dev/ttyACM1"}, {"label": "ESP32", "tty": "/dev/ttyUSB0"}]},
                                {"rails": {"USB": "on"}}, "Gamepad: button 0 (A) pressed")
        self.assertEqual(rows[0], ("FLIPPER", "ttyACM1", "present"))
        self.assertEqual(rows[1][0], "GAMEPAD")
        self.assertEqual(rows[1][2], "present")
        self.assertEqual(rows[2], ("USB RAIL", "on", "present"))
        self.assertEqual(rows[3], ("ESP32", "ttyUSB0", "present"))

    def test_companion_rows_when_nothing_is_there(self):
        rows = h.companion_rows({}, {"rails": {"USB": "off"}}, "Gamepad: waiting for a button.")
        self.assertEqual([r[2] for r in rows], ["absent", "waiting", "absent"])
        self.assertEqual(h.companion_rows(None, None, "")[0][2], "absent")
        self.assertEqual(h.companion_rows({}, None, "", flip_shown=True)[0][2], "present")

    def test_the_internal_card_follows_the_usb_rail(self):
        on, off = {"rails": {"USB": "on"}}, {"rails": {"USB": "off"}}
        self.assertEqual(h.companion_rows({}, on, "", card=True)[3], (h.CARD_NAME, "ready", "present"))
        self.assertEqual(h.companion_rows({}, on, "", card=False)[3][2], "waiting")
        self.assertEqual(h.companion_rows({}, off, "", card=False)[3], (h.CARD_NAME, "off with the USB rail", "absent"))
        self.assertEqual(len(h.companion_rows({}, off, "")), 3)                 # not asked: no row

    def test_the_flipper_key_is_only_listed_while_its_tab_is_there(self):
        with_it = [text for _, text in h.key_rows(["Home", "Flipper", "Help"])]
        without = [text for _, text in h.key_rows(["Home", "Help"])]
        self.assertTrue(any("Flipper" in text for text in with_it))
        self.assertFalse(any("Flipper" in text for text in without))
        self.assertEqual(len(with_it), len(h.KEYS))
        self.assertEqual(len(without), len(h.KEYS) - 1)
        self.assertEqual(len(h.key_rows(None)), len(h.KEYS) - 1)

    def test_each_rail_key_says_whether_its_rail_is_on(self):
        rows = h.rail_rows({"rails": {"GPS": "off", "LORA": "on", "SDR": "off", "USB": "on"}})
        self.assertEqual([state for _, _, state in rows], ["OFF", "ON", "OFF", "ON", ""])
        self.assertEqual([text for _, text, _ in rows], [text for _, text in h.RAILS])
        self.assertEqual([state for _, _, state in h.rail_rows(None)], [""] * len(h.RAILS))
        self.assertEqual(h.rail_rows({"rails": {"GPS": "switching"}})[0][2], "")     # not on, not off: say nothing

    def test_other_devices_are_listed_under_the_name_aiov2_ctl_gives_the_list(self):
        rows = h.companion_rows({"usb_rail": "on", "devices": [{"label": "ESP32", "tty": "/dev/ttyUSB0"}]},
                                {"rails": {"USB": "on"}}, "")
        self.assertIn(("ESP32", "ttyUSB0", "present"), rows)


if __name__ == "__main__":
    unittest.main()
