import unittest

from ops_gui import drawer as d


class ActivityTests(unittest.TestCase):
    def test_entries_are_stamped_and_bounded(self):
        log = d.ActivityLog(limit=3)
        for i in range(5):
            log.add(f"event {i}", now=0)
        self.assertEqual([e[2] for e in log.entries], ["event 2", "event 3", "event 4"])
        self.assertRegex(log.lines()[0], r"^\d\d:\d\d:\d\d  event 2$")
        self.assertEqual(log.lines(1), [log.lines()[-1]])

    def test_blank_and_multiline_text(self):
        log = d.ActivityLog()
        log.add("")
        log.add("   ")
        log.add("two\nlines   here")
        self.assertEqual(len(log.entries), 1)
        self.assertEqual(log.entries[0][2], "two lines here")

    def test_listeners_hear_new_entries_and_cannot_break_it(self):
        log, heard = d.ActivityLog(), []
        log.listeners.append(heard.append)
        log.listeners.append(lambda e: 1 / 0)
        log.add("hello")
        self.assertEqual(heard[0][2], "hello")


class TextTests(unittest.TestCase):
    def test_ansi_is_removed(self):
        self.assertEqual(d.strip_ansi("\x1b[31mred\x1b[0m and \x1b]0;title\x07plain"), "red and plain")
        self.assertEqual(d.strip_ansi(None), "")

    def test_clean_output(self):
        self.assertEqual(d.clean_output("a\r\nb\rc\x00"), "a\nb\nc")
        self.assertEqual(d.clean_output("abc\x08\x08d"), "ad")
        self.assertEqual(d.clean_output("\x08\x08x"), "x")

    def test_shell_input_is_one_command_with_a_marker(self):
        self.assertEqual(d.shell_input("ls -l"), f"ls -l\necho {d.SHELL_MARK} $?\n".encode())
        once = d.shell_input("echo a\nrm -rf x\r\n")
        self.assertEqual(once.count(b"\n"), 2)            # newlines inside a command cannot start a second one

    def test_filter_hides_the_marker_and_reports_codes(self):
        f = d.ShellFilter()
        text, codes = f.feed(f"hello\n{d.SHELL_MARK} 0\nfail\n{d.SHELL_MARK} 2\n")
        self.assertEqual((text, codes), ("hello\nfail\n", [0, 2]))

    def test_filter_handles_split_chunks(self):
        f = d.ShellFilter()
        self.assertEqual(f.feed("par"), ("", []))
        self.assertEqual(f.feed(f"tial\n{d.SHELL_MARK}"), ("partial\n", []))
        self.assertEqual(f.feed(" 1\n"), ("", [1]))
        self.assertEqual(f.feed(f"{d.SHELL_MARK} x\n"), ("", [-1]))


class HistoryTests(unittest.TestCase):
    def test_up_and_down(self):
        h = d.History()
        self.assertEqual((h.previous(), h.next()), ("", ""))
        for c in ("one", "two", "two", "", "three"):
            h.add(c)
        self.assertEqual(h.items, ["one", "two", "three"])
        self.assertEqual([h.previous(), h.previous(), h.previous(), h.previous()], ["three", "two", "one", "one"])
        self.assertEqual([h.next(), h.next(), h.next(), h.next()], ["two", "three", "", ""])

    def test_bounded(self):
        h = d.History(limit=2)
        for c in "abc":
            h.add(c)
        self.assertEqual(h.items, ["b", "c"])


class SizeTests(unittest.TestCase):
    def test_height(self):
        self.assertEqual(d.drawer_height(720), 302)
        self.assertEqual(d.drawer_height(300), 200)
        self.assertEqual(d.drawer_height(2000), 380)


if __name__ == "__main__":
    unittest.main()
