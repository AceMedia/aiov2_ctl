import unittest

from ops_gui import flipper_panel as f


class ModeTests(unittest.TestCase):
    def test_usable(self):
        self.assertTrue(f.usable({"mode": "cli", "device": {"name": "x"}}))
        self.assertFalse(f.usable({"mode": "bridge", "device": {}}))
        self.assertFalse(f.usable({"mode": "cli", "device": {}}))
        self.assertFalse(f.usable(None))

    def test_notes(self):
        self.assertIn("Press Back", f.mode_note({"mode": "bridge"}))
        self.assertEqual(f.mode_note({"mode": "cli", "device": {"a": 1}}), "")
        self.assertIn("No answer", f.mode_note({}))


class PathTests(unittest.TestCase):
    def test_sd_only(self):
        for good in ("/ext", "/ext/apps", "/ext/subghz/My File.sub"):
            self.assertTrue(f.is_sd_path(good), good)
        for bad in ("/int", "/ext/../int", "/extra", "", None, "/ext/a;b", "ext"):
            self.assertFalse(f.is_sd_path(bad), bad)

    def test_parent_never_leaves_the_card(self):
        self.assertEqual(f.parent_path("/ext/apps/GPIO"), "/ext/apps")
        self.assertEqual(f.parent_path("/ext/apps"), "/ext")
        self.assertEqual(f.parent_path("/ext"), "/ext")
        self.assertEqual(f.parent_path("/int/x"), "/ext")

    def test_join(self):
        self.assertEqual(f.join_path("/ext", "apps"), "/ext/apps")

    def test_ls_args_fall_back_to_the_card(self):
        self.assertEqual(f.ls_args("/int")[-1], "/ext")
        self.assertEqual(f.ls_args("/ext/nfc")[-1], "/ext/nfc")


class ParseTests(unittest.TestCase):
    def test_ls(self):
        text = "-  b.txt  123b\nd  apps\n-  A file.sub  2kb\n(empty)\n"
        self.assertEqual(f.parse_ls(text), [("dir", "apps", ""), ("file", "A file.sub", "2kb"),
                                           ("file", "b.txt", "123b")])
        self.assertEqual(f.parse_ls("(empty)"), [])
        self.assertEqual(f.parse_ls(None), [])

    def test_apps(self):
        text = "Applications:\n\tNFC\n\tMarauder\n\n\tNFC\nError: nope\n"
        self.assertEqual(f.parse_apps(text), ["Marauder", "NFC"])

    def test_errors_and_saved(self):
        self.assertEqual(f.error_text("x\nError: No Flipper found."), "No Flipper found.")
        self.assertEqual(f.error_text("fine"), "")
        self.assertEqual(f.saved_line("Saved 5 bytes to /x/y"), "Saved 5 bytes to /x/y")


class SafetyTests(unittest.TestCase):
    def test_commands_are_the_cli_ones(self):
        self.assertEqual(f.get_args("/ext/a")[:3], ["aiov2_ctl", "--flipper", "get"])
        self.assertEqual(f.open_args("NFC"), ["aiov2_ctl", "--flipper", "open", "NFC"])

    def test_no_write_commands(self):
        import inspect
        src = inspect.getsource(f)
        for word in ("--force", "input_event", "rm ", "delete", "format"):
            self.assertNotIn(word, src)
        self.assertEqual(src.count('"--flipper", "put"'), 1)        # one place builds the send command
        self.assertEqual(src.count("put_args("), 2)                 # its definition and the confirmed send
        self.assertEqual(src.count("install_args("), 2)

    def test_launching_warns(self):
        self.assertIn("transmit", f.open_warning("Sub-GHz"))


if __name__ == "__main__":
    unittest.main()


class ExtendTests(unittest.TestCase):
    def test_names(self):
        for good in ("notes.txt", "My Folder", "a-b_c (1)", "air.fap"):
            self.assertTrue(f.valid_name(good), good)
        for bad in ("", "  ", "../x", "a/b", ".hidden", "a;b", "x" * 80, "a..b", None):
            self.assertFalse(f.valid_name(bad), bad)

    def test_send_and_install_commands(self):
        self.assertEqual(f.put_args("/home/u/a.txt", "/ext/nfc"),
                         ["aiov2_ctl", "--flipper", "put", "/home/u/a.txt", "/ext/nfc/a.txt"])
        self.assertEqual(f.mkdir_args("/ext/new"), ["aiov2_ctl", "--flipper", "mkdir", "/ext/new"])
        self.assertEqual(f.install_args("/x/air.fap", "GPIO"),
                         ["aiov2_ctl", "--flipper", "install", "/x/air.fap", "--category", "GPIO"])
        self.assertEqual(f.install_args("/x/a.fap", "Nonsense")[-1], "Tools")

    def test_gpio_commands(self):
        self.assertEqual(f.gpio_read_args("PC0"), ["aiov2_ctl", "--flipper", "gpio", "read", "PC0"])
        self.assertEqual(f.gpio_set_args("PB2", 1)[-3:], ["PB2", "1", "--yes"])
        self.assertEqual(f.gpio_set_args("PB2", 0)[-2], "0")
        self.assertEqual(len(f.GPIO_PINS), 8)

    def test_gpio_reply(self):
        self.assertEqual(f.parse_gpio_reply("PC0 reads 1"), (1, ""))
        self.assertEqual(f.parse_gpio_reply("Error: Close the app"), (None, "Close the app"))
        self.assertEqual(f.parse_gpio_reply(""), (None, ""))

    def test_warnings_name_the_thing(self):
        self.assertIn("PC0 high", f.gpio_warning("PC0", 1))
        self.assertIn("voltage", f.gpio_warning("PC0", 0))
        self.assertIn("not replaced", f.sd_send_warning("a.txt", "/ext"))

    def test_catalogue_is_just_advice(self):
        names = [n for n, _, _ in f.CATALOG]
        self.assertIn("Air Mouse", names)
        self.assertIn("IMU", dict((n, needs) for n, _, needs in f.CATALOG)["Air Mouse"])
        self.assertNotIn("http", " ".join(" ".join(c) for c in f.CATALOG))
        self.assertIn("lab.flipper.net/apps", f.CATALOG_HINT)

    def test_no_attack_words_in_the_panel(self):
        import inspect
        src = inspect.getsource(f).lower()
        for word in ("badusb", "ble spam", "blespam", "jam", "deauth", "evil"):
            self.assertNotIn(word, src)
