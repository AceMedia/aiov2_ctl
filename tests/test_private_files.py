"""Files root may write into a user's folder (the rails mark, the kept status, the survey capture folder), a
status that must not be kept when a rail was switched while it was read, and the one sender --wifi-listen always
reports."""
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiov2_ctl as a  # noqa: E402


class Folder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = self.dir.name
        self.mark = os.path.join(self.home, "x.rails")
        self.status = os.path.join(self.home, "x.status.json")
        self.victim = os.path.join(self.home, "victim")           # stands for a file root could reach
        os.mkdir(self.victim)
        self.target = os.path.join(self.victim, "important")
        with open(self.target, "w") as f:
            f.write("keep me")

    def target_text(self):
        with open(self.target) as f:
            return f.read()


class PrivateWriteTests(Folder):
    def test_the_mark_and_the_status_are_written_whole_and_leave_nothing_behind(self):
        self.assertTrue(a.mark_rails_changed(self.mark, now=5.0))
        self.assertTrue(a.write_shared_status({"rails": {}}, 5.0, self.status))
        self.assertEqual(sorted(os.listdir(self.home)), ["victim", "x.rails", "x.status.json"])
        self.assertEqual(open(self.mark).read(), "5.000\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.status).st_mode), 0o600)

    def test_a_link_planted_at_the_old_temporary_name_is_not_followed(self):
        for path in (self.mark, self.status):
            os.symlink(self.target, f"{path}.{os.getpid()}.tmp")
        self.assertTrue(a.mark_rails_changed(self.mark))
        self.assertTrue(a.write_shared_status({"rails": {}}, 5.0, self.status))
        self.assertEqual(self.target_text(), "keep me")

    def test_a_link_planted_at_the_real_name_is_replaced_not_written_through(self):
        os.symlink(self.target, self.mark)
        os.symlink(self.target, self.status)
        self.assertTrue(a.mark_rails_changed(self.mark))
        self.assertTrue(a.write_shared_status({"rails": {}}, 5.0, self.status))
        self.assertEqual(self.target_text(), "keep me")
        self.assertFalse(os.path.islink(self.mark))
        self.assertFalse(os.path.islink(self.status))

    def test_the_temporary_name_cannot_be_guessed(self):
        names = []
        real = os.open

        def spy(path, flags, mode=0o777, *, dir_fd=None):
            if dir_fd is not None:
                names.append((path, flags))
            return real(path, flags, mode, dir_fd=dir_fd)
        with mock.patch.object(a.os, "open", spy):
            a.mark_rails_changed(self.mark)
            a.mark_rails_changed(self.mark)
        self.assertEqual(len(names), 2)
        self.assertNotEqual(names[0][0], names[1][0])
        for name, flags in names:
            self.assertNotIn(str(os.getpid()), name.split(".")[-2])
            self.assertTrue(flags & os.O_EXCL and flags & os.O_NOFOLLOW and flags & os.O_CREAT)
            self.assertFalse(flags & os.O_TRUNC)

    def test_a_folder_others_can_write_into_is_refused(self):
        for mode in (0o770, 0o707, 0o777, 0o1777):
            os.chmod(self.home, mode)
            self.assertFalse(a.mark_rails_changed(self.mark), oct(mode))
            self.assertFalse(a.write_shared_status({"rails": {}}, 5.0, self.status), oct(mode))
        os.chmod(self.home, 0o700)
        self.assertEqual(sorted(os.listdir(self.home)), ["victim"])

    def test_a_folder_that_belongs_to_someone_else_is_refused(self):
        other = os.getuid() + 1
        with self.assertRaises(OSError):
            a.write_private_file(self.mark, "x", uid=other)
        self.assertEqual(os.listdir(self.home), ["victim"])

    def test_a_folder_that_is_really_a_link_is_refused(self):
        link = os.path.join(self.home, "link")
        os.symlink(self.victim, link)
        self.assertFalse(a.mark_rails_changed(os.path.join(link, "x.rails")))
        self.assertEqual(os.listdir(self.victim), ["important"])

    def test_as_root_for_a_user_the_owner_is_changed_through_the_open_file_never_by_name(self):
        me, calls = os.getuid(), []
        with mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.dict(a.os.environ, {"SUDO_UID": str(me)}), \
                mock.patch.object(a.os, "fchown", lambda fd, uid, gid: calls.append((type(fd), uid, gid))), \
                mock.patch.object(a.os, "chown", side_effect=AssertionError("owner changed by name")):
            self.assertTrue(a.mark_rails_changed(self.mark))
            self.assertTrue(a.write_shared_status({"rails": {}}, 5.0, self.status))
        self.assertEqual(calls, [(int, me, -1), (int, me, -1)])

    def test_as_root_a_folder_that_is_not_the_calling_users_is_refused(self):
        with mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.dict(a.os.environ, {"SUDO_UID": str(os.getuid() + 1)}), \
                mock.patch.object(a.os, "fchown", side_effect=AssertionError("must not get this far")):
            self.assertFalse(a.mark_rails_changed(self.mark))
        self.assertEqual(os.listdir(self.home), ["victim"])

    def test_a_failure_part_way_removes_the_temporary_file(self):
        with mock.patch.object(a.os, "write", side_effect=OSError("disk full")):
            self.assertFalse(a.mark_rails_changed(self.mark))
        with mock.patch.object(a.os, "replace", side_effect=OSError("no")):
            self.assertFalse(a.write_shared_status({"rails": {}}, 5.0, self.status))
        self.assertEqual(os.listdir(self.home), ["victim"])

    def test_whose_files_they_are(self):
        self.assertEqual(a.shared_owner_uid({"SUDO_UID": "1000"}, 0), 1000)
        self.assertEqual(a.shared_owner_uid({}, 0), 0)
        self.assertEqual(a.shared_owner_uid({"SUDO_UID": "rubbish"}, 0), 0)
        self.assertEqual(a.shared_owner_uid({"SUDO_UID": "1000"}, 1001), 1001)   # not root: the claim counts for nothing


class PrivateReadTests(Folder):
    def keep(self, path=None):
        with open(path or self.status, "w") as f:
            json.dump({"written": 100.0, "status": {"rails": {"GPS": "on"}}}, f)
        os.chmod(path or self.status, 0o600)      # as the tool writes it, whatever this machine's umask is

    def read(self):
        return a.read_shared_status(2.0, 100.5, self.status, self.mark)

    def test_an_ordinary_kept_status_is_read(self):
        self.keep()
        self.assertEqual(self.read(), {"rails": {"GPS": "on"}})

    def test_a_link_is_not_read(self):
        self.keep(self.target)
        os.symlink(self.target, self.status)
        self.assertIsNone(self.read())

    def test_a_file_others_can_write_is_not_trusted(self):
        self.keep()
        os.chmod(self.status, 0o666)
        self.assertIsNone(self.read())

    def test_a_file_that_belongs_to_someone_else_is_not_trusted_but_roots_is(self):
        self.keep()
        me = os.getuid()
        with mock.patch.object(a, "shared_owner_uid", return_value=me + 1):
            self.assertEqual(self.read() is not None, me == 0)
        real = os.fstat(os.open(self.status, os.O_RDONLY))

        class AsRoot:
            st_mode, st_uid = real.st_mode, 0
        with mock.patch.object(a, "shared_owner_uid", return_value=me + 1), \
                mock.patch.object(a.os, "fstat", return_value=AsRoot):
            self.assertIsNotNone(self.read())

    def test_a_pipe_does_not_hang_the_reader(self):
        os.mkfifo(self.status)
        self.assertIsNone(self.read())


class SwitchedWhileReadingTests(Folder):
    def test_a_rail_switched_while_the_board_was_read_is_returned_once_but_not_kept(self):
        reads = []

        def build():
            reads.append(1)
            if len(reads) == 1:
                a.mark_rails_changed(self.mark)                  # someone switches a rail mid-read
                os.utime(self.mark, (50.0, 50.0))                # even with a clock that makes the mark look old
            return {"rails": {"GPS": "off"}, "n": len(reads)}
        first = a.shared_status(2.0, build, 100.0, self.status, self.mark)
        self.assertEqual(first["n"], 1)
        self.assertFalse(os.path.exists(self.status))
        self.assertEqual(a.shared_status(2.0, build, 100.2, self.status, self.mark)["n"], 2)
        self.assertEqual(a.shared_status(2.0, build, 100.4, self.status, self.mark)["n"], 2)   # that one is shared

    def test_the_first_mark_ever_appearing_mid_read_counts_as_a_switch(self):
        def build():
            a.mark_rails_changed(self.mark)
            return {"rails": {}}
        a.shared_status(0, build, 100.0, self.status, self.mark)
        self.assertFalse(os.path.exists(self.status))

    def test_an_older_kept_status_is_not_left_looking_current(self):
        a.shared_status(2.0, lambda: {"rails": {}, "n": "old"}, 90.0, self.status, self.mark)

        def build():
            a.mark_rails_changed(self.mark)
            os.utime(self.mark, (100.1, 100.1))
            return {"rails": {}, "n": "mid"}
        a.shared_status(2.0, build, 100.0, self.status, self.mark)
        self.assertIsNone(a.read_shared_status(2.0, 100.2, self.status, self.mark))


class CaptureFolderTests(Folder):
    def test_the_folder_is_made_private_and_a_capture_is_created_inside_it(self):
        folder = os.path.join(self.home, "aiov2-captures")
        dfd = a.capture_folder_fd(folder)
        self.addCleanup(os.close, dfd)
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
        w = a.PcapWriter("s.pcap", dir_fd=dfd)
        w.close()
        self.assertEqual(os.listdir(folder), ["s.pcap"])

    def test_a_link_in_place_of_the_folder_is_refused_and_nothing_changes_owner(self):
        folder = os.path.join(self.home, "aiov2-captures")
        os.symlink(self.victim, folder)

        class Owner:
            pw_uid, pw_gid = os.getuid(), os.getgid()
        with mock.patch.object(a.os, "fchown", side_effect=AssertionError("owner changed")), \
                mock.patch.object(a.os, "chown", side_effect=AssertionError("owner changed by name")):
            with self.assertRaises(OSError):
                a.capture_folder_fd(folder, Owner)
        self.assertEqual(os.listdir(self.victim), ["important"])

    def test_a_folder_others_can_write_or_that_is_someone_elses_is_refused(self):
        folder = os.path.join(self.home, "aiov2-captures")
        os.mkdir(folder, 0o700)
        os.chmod(folder, 0o777)
        with self.assertRaises(OSError):
            a.capture_folder_fd(folder)
        os.chmod(folder, 0o700)

        class Other:
            pw_uid, pw_gid = os.getuid() + 1, os.getgid()
        with mock.patch.object(a.os, "fchown", side_effect=AssertionError("an existing folder is never handed over")):
            with self.assertRaises(OSError):
                a.capture_folder_fd(folder, Other)

    def test_root_hands_over_only_a_folder_it_has_just_made_and_through_the_open_folder(self):
        folder, calls = os.path.join(self.home, "aiov2-captures"), []
        me = os.getuid()

        class Owner:
            pw_uid, pw_gid = me + 1, 7
        real = os.fstat

        def fstat(fd):                     # after the hand-over the folder is the user's
            st = real(fd)

            class St:
                st_mode, st_uid = st.st_mode, (Owner.pw_uid if calls else st.st_uid)
            return St
        with mock.patch.object(a.os, "fchown", lambda fd, uid, gid: calls.append((uid, gid))), \
                mock.patch.object(a.os, "fstat", fstat), \
                mock.patch.object(a.os, "chown", side_effect=AssertionError("owner changed by name")):
            os.close(a.capture_folder_fd(folder, Owner))
        self.assertEqual(calls, [(me + 1, 7)])

    def test_a_capture_is_never_written_through_a_link(self):
        os.symlink(self.target, os.path.join(self.home, "s.pcap"))
        with self.assertRaises(OSError):
            a.PcapWriter(os.path.join(self.home, "s.pcap"))
        self.assertEqual(self.target_text(), "keep me")


class OnlyOneSenderTests(unittest.TestCase):
    QUIET = "aa:bb:cc:dd:ee:ff"

    def crowd(self, senders=80):
        seen = [(f"02:00:00:00:00:{i:02x}", -50, 9.5) for i in range(senders) for _ in range(i + 2)]
        return seen + [(self.QUIET, -71, 9.9)]

    def test_the_chosen_quiet_sender_is_always_reported_with_its_readings(self):
        links = a.monitor_summary(self.crowd(), 10.0, only=self.QUIET)["links"]
        self.assertEqual(links[self.QUIET][:2], [1, -71])
        self.assertEqual(links[self.QUIET][2], [[-0.1, -71]])

    def test_without_a_choice_the_quietest_of_a_big_crowd_is_still_left_out(self):
        links = a.monitor_summary(self.crowd(), 10.0)["links"]
        self.assertNotIn(self.QUIET, links)
        self.assertEqual(len(links), a.MONITOR_LINKS_PER_SECOND + a.MONITOR_BRIEF_LINKS)

    def test_a_quiet_sender_among_a_normal_number_no_longer_vanishes(self):
        links = a.monitor_summary(self.crowd(20), 10.0)["links"]
        self.assertEqual(links[self.QUIET], [1, -71])              # listed, briefly: frames and average
        full = [m for m, e in links.items() if len(e) > 2]
        self.assertEqual(len(full), a.MONITOR_LINKS_PER_SECOND)   # only the busiest carry every reading

    def test_a_chosen_sender_not_heard_is_simply_absent(self):
        self.assertNotIn(self.QUIET, a.monitor_summary([("02:00:00:00:00:01", -50)], 1.0, only=self.QUIET)["links"])

    def run_cmd(self, args, stdin=""):
        out, got = io.StringIO(), {}

        def fake_run(iface, channel, seconds, emit, only=None):
            got.update(iface=iface, channel=channel, only=only)
        with mock.patch.object(a, "monitor_run", fake_run), mock.patch.object(a.sys, "stdin", io.StringIO(stdin)), \
                redirect_stdout(out), redirect_stderr(out):
            code = a.monitor_command(args)
        return code, got

    def test_the_address_can_be_given_as_an_argument_or_on_standard_input(self):
        self.assertEqual(self.run_cmd(["--channel", "6", "--only", "AA:BB:CC:DD:EE:FF", "--json"]),
                         (0, {"iface": "wlan1", "channel": 6, "only": self.QUIET}))
        self.assertEqual(self.run_cmd(["--channel", "6", "--only", "-", "--json"], "AA:bb:cc:dd:ee:ff\n")[1]["only"],
                         self.QUIET)
        self.assertIsNone(self.run_cmd(["--channel", "6", "--json"])[1]["only"])

    def test_anything_that_is_not_exactly_an_address_is_refused(self):
        for bad in ("aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:ff:00", "aa-bb-cc-dd-ee-ff", "zz:bb:cc:dd:ee:ff",
                    "aa:bb:cc:dd:ee:ff;id", "aa:bb:cc:dd:ee:ff --restore", "*"):
            self.assertEqual(self.run_cmd(["--only", bad]), (1, {}), bad)
            self.assertEqual(self.run_cmd(["--only", "-"], bad + "\n"), (1, {}), bad)
        self.assertEqual(self.run_cmd(["--only", "-"], ""), (1, {}))
        self.assertEqual(self.run_cmd(["--only"]), (1, {}))

    def test_the_password_free_rules_stay_exact_with_no_address_or_wildcard_in_them(self):
        blob = a.build_sudoers("shane")
        self.assertIn(f"--wifi-listen --iface wlan1 --channel 6 --seconds {a.MONITOR_SECONDS} --only - --json", blob)
        for line in blob.splitlines():
            if "--only" in line:
                self.assertRegex(line, r"--only - --json\b")
                self.assertNotIn("*", line.split("--wifi-listen", 1)[1])


if __name__ == "__main__":
    unittest.main()
