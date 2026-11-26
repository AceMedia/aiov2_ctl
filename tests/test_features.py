"""Settings > Features: the decisions the page makes. The page itself is only checked on the real screen."""
import json
import unittest

from ops_gui import features as f


def row(key, title=None, **changes):
    base = {"key": key, "title": title or key.title(), "summary": "What it is.", "required": False,
            "installed": False, "missing": [], "adds": [], "removes": [], "hardware": "", "hardware_found": None}
    base.update(changes)
    return base


def report(*rows, root="sudo"):
    return json.dumps({"version": 1, "root": root, "features": list(rows)})


class ReportTests(unittest.TestCase):
    def test_a_report_is_read_as_it_was_written(self):
        got = f.parse_report(report(row("core", required=True, installed=True), row("gps")))
        self.assertEqual(got["root"], "sudo")
        self.assertEqual([r["key"] for r in got["features"]], ["core", "gps"])
        self.assertEqual(f.titles(got), {"core": "Core", "gps": "Gps"})

    def test_an_older_command_line_is_not_mistaken_for_a_report(self):
        for text in ("", "Usage: aiov2_ctl --features", "feature    default  installed", "[]", "{}", None,
                     json.dumps({"features": [{"key": "gps"}]}), json.dumps({"features": ["gps"]})):
            self.assertIsNone(f.parse_report(text), text)
        self.assertIn("aiov2_ctl --update", f.OLD_CLI)
        self.assertNotIn("sudo aiov2_ctl --update", f.OLD_CLI)        # --update refuses to run as root

    def test_an_unfinished_update_is_passed_on_and_is_false_when_not_said(self):
        self.assertIs(f.parse_report(report(row("gps")))["unfinished"], False)
        text = json.dumps({"version": 1, "root": "sudo", "unfinished": True, "features": [row("gps")]})
        self.assertIs(f.parse_report(text)["unfinished"], True)
        text = json.dumps({"version": 1, "root": "sudo", "unfinished": "yes", "features": [row("gps")]})
        self.assertIs(f.parse_report(text)["unfinished"], False)

    def test_update_runs_as_the_user_in_a_terminal(self):
        self.assertEqual(f.UPDATE_ARGV[-1], "aiov2_ctl --update")
        self.assertNotIn("sudo", " ".join(f.UPDATE_ARGV))

    def test_an_unknown_way_to_get_root_counts_as_none(self):
        self.assertIsNone(f.parse_report(report(row("gps"), root="doas"))["root"])
        self.assertIsNone(f.parse_report(report(row("gps"), root=None))["root"])
        self.assertEqual(f.parse_report(report(row("gps"), root="pkexec"))["root"], "pkexec")


class RowTests(unittest.TestCase):
    def test_the_state_says_installed_missing_and_hardware(self):
        self.assertEqual(f.state_text(row("gps")), "Not installed.")
        self.assertEqual(f.state_text(row("gps", installed=True)), "Installed.")
        self.assertEqual(f.state_text(row("mesh", missing=["the package meshtasticd"])),
                         "Not installed, missing: the package meshtasticd.")
        self.assertEqual(f.state_text(row("tray", installed=True, missing=["the tray shortcut", "its icons"])),
                         "Installed, missing: the tray shortcut, its icons.")
        self.assertEqual(f.state_text(row("sdr", hardware="the AIO board", hardware_found=False)),
                         "Not installed. The AIO board not found now.")
        self.assertEqual(f.state_text(row("sdr", installed=True, hardware="the AIO board", hardware_found=True)),
                         "Installed.")
        self.assertEqual(f.state_text(row("sdr", installed=True, hardware="the AIO board", hardware_found=None)),
                         "Installed.")

    def test_what_is_not_installed_can_be_added_and_what_is_can_be_removed(self):
        self.assertEqual(f.action(row("gps")), "add")
        self.assertEqual(f.action(row("gps", installed=True)), "remove")

    def test_the_controller_and_the_running_app_have_no_remove_button(self):
        core = row("core", required=True, installed=True)
        app = row("app", installed=True)
        self.assertIsNone(f.action(core))
        self.assertIsNone(f.action(app))
        self.assertIn("everything else needs it", f.kept_reason(core))
        self.assertIn("sudo aiov2_ctl --uninstall --only app", f.kept_reason(app))
        self.assertEqual(f.kept_reason(row("gps", installed=True)), "")

    def test_the_app_can_still_be_added_when_it_is_not_recorded(self):
        app = row("app")
        self.assertEqual(f.action(app), "add")
        self.assertEqual(f.kept_reason(app), "")


class ConfirmTests(unittest.TestCase):
    names = {"sdr": "Software radio", "adsb": "Aircraft receiver", "autostart": "Tray at login", "tray": "Tray"}

    def test_adding_says_what_comes_with_it(self):
        self.assertEqual(f.confirm_text(row("gps", "GPS"), self.names, "add"), "Add GPS?")
        text = f.confirm_text(row("adsb", "Aircraft receiver", adds=["sdr"]), self.names, "add")
        self.assertIn("Add Aircraft receiver?", text)
        self.assertIn("also adds what it needs: Software radio.", text)

    def test_adding_warns_when_the_hardware_was_not_found(self):
        text = f.confirm_text(row("flipper", "Flipper", hardware="a Flipper Zero", hardware_found=False), {}, "add")
        self.assertIn("A Flipper Zero was not found just now", text)
        self.assertNotIn("not found", f.confirm_text(row("flipper", hardware="a Flipper Zero"), {}, "add"))

    def test_removing_says_what_goes_with_it_and_what_stays(self):
        text = f.confirm_text(row("tray", "Tray", installed=True, removes=["autostart"]), self.names, "remove")
        self.assertIn("Remove Tray?", text)
        self.assertIn("also removes what cannot work without it: Tray at login.", text)
        self.assertIn("Packages, rails and your saved settings are left as they are.", text)
        self.assertNotIn("also removes", f.confirm_text(row("gps", installed=True), self.names, "remove"))

    def test_a_feature_with_no_known_title_is_named_by_its_key(self):
        self.assertIn("mystery", f.confirm_text(row("x", removes=["mystery"], installed=True), {}, "remove"))


class CommandTests(unittest.TestCase):
    def test_one_feature_is_changed_and_nothing_is_asked_again(self):
        self.assertEqual(f.change_argv("sudo", "add", "gps"),
                         ["sudo", "-n", "/usr/local/bin/aiov2_ctl", "--install", "--only", "gps", "--yes"])
        self.assertEqual(f.change_argv("pkexec", "remove", "tray"),
                         ["pkexec", "/usr/local/bin/aiov2_ctl", "--uninstall", "--only", "tray", "--yes"])

    def test_sudo_never_waits_for_a_password(self):
        self.assertEqual(f.change_argv("sudo", "remove", "gps")[:2], ["sudo", "-n"])

    def test_with_no_way_to_get_root_there_is_no_command_only_words(self):
        self.assertIsNone(f.change_argv(None, "add", "gps"))
        self.assertIsNone(f.change_argv("sudo", "purge", "gps"))
        self.assertEqual(f.command_text("add", "gps"), "sudo aiov2_ctl --install --only gps")
        self.assertEqual(f.command_text("remove", "gps"), "sudo aiov2_ctl --uninstall --only gps")

    def test_the_list_is_read_without_root(self):
        self.assertEqual(f.REPORT_ARGS, ["aiov2_ctl", "--features", "--json"])


class ResultTests(unittest.TestCase):
    def test_how_it_ended_is_said_plainly(self):
        self.assertEqual(f.result_text("add", "GPS", 0), "GPS added.")
        self.assertEqual(f.result_text("remove", "GPS", 0), "GPS removed.")
        said = "Result\n------\n  core       already there\n  mesh       not installed: meshtasticd is not in ...\n"
        self.assertTrue(f.left_out(said))
        self.assertFalse(f.left_out("Result\n------\n  core       already there\n  gps        installed\n"))
        self.assertFalse(f.left_out(""))
        self.assertIn("not everything could be installed", f.result_text("add", "LoRa mesh", 0, True))
        self.assertEqual(f.result_text("remove", "LoRa mesh", 0, True), "LoRa mesh removed.")
        self.assertIn("nothing was changed", f.result_text("add", "GPS", 126))
        self.assertIn("nothing was changed", f.result_text("remove", "GPS", 127))
        self.assertIn("could not be started", f.result_text("add", "GPS", None))
        self.assertIn("did not finish (exit 1)", f.result_text("remove", "GPS", 1))

    def test_only_the_end_of_a_long_output_is_kept(self):
        text = "\n".join(str(i) for i in range(100))
        self.assertEqual(f.tail(text, 3), "97\n98\n99")
        self.assertEqual(f.tail("", 3), "")
        self.assertEqual(f.tail(None), "")



def channel(branch="main", name="stable", incoming=(), beta=True, error=""):
    return json.dumps({"version": 1, "channel": name, "branch": branch, "incoming": list(incoming) if incoming is not None else None,
                       "root": "sudo", "error": error,
                       "channels": [{"name": "stable", "branch": "main", "available": True, "current": branch == "main"},
                                    {"name": "beta", "branch": "beta", "available": beta, "current": branch == "beta"}]})


class ChannelTests(unittest.TestCase):
    """The Updates card: the channel, what is waiting, updating and switching from inside the app."""

    def test_the_channel_report_is_read_and_anything_else_is_none(self):
        ch = f.parse_channel(channel(incoming=["abc one"]))
        self.assertEqual((ch["channel"], ch["branch"], ch["incoming"], ch["error"]), ("stable", "main", ["abc one"], ""))
        self.assertEqual(ch["channels"], [{"name": "stable", "available": True, "current": True},
                                          {"name": "beta", "available": True, "current": False}])
        for text in ("", "Usage: aiov2_ctl ...", "[]", json.dumps({"channels": "x", "branch": "main"}),
                     json.dumps({"channels": [], "branch": 3}), json.dumps({"channels": ["beta"], "branch": "main"})):
            self.assertIsNone(f.parse_channel(text), text)

    def test_unknown_waiting_changes_stay_unknown(self):
        self.assertIsNone(f.parse_channel(channel(incoming=None))["incoming"])

    def test_the_line_says_the_channel_and_what_is_waiting(self):
        self.assertEqual(f.channel_text(f.parse_channel(channel())), "Channel: stable. Up to date.")
        self.assertEqual(f.channel_text(f.parse_channel(channel(incoming=["a x", "b y"]))), "Channel: stable. 2 changes waiting.")
        self.assertEqual(f.channel_text(f.parse_channel(channel(incoming=["a x"]))), "Channel: stable. 1 change waiting.")
        self.assertEqual(f.channel_text(f.parse_channel(channel(incoming=None, error="The repository could not be reached just now."))),
                         "Channel: stable. The repository could not be reached just now.")
        self.assertIn("its own branch, my-work", f.channel_text(f.parse_channel(channel("my-work", None))))

    def test_the_waiting_changes_are_listed_without_commit_names_and_cut_short(self):
        ch = f.parse_channel(channel(incoming=[f"abc{i} change {i}" for i in range(9)]))
        lines = f.incoming_text(ch).splitlines()
        self.assertEqual(lines[0], "change 0")
        self.assertEqual((len(lines), lines[-1]), (7, "and 3 more"))
        self.assertEqual(f.incoming_text(f.parse_channel(channel())), "")

    def test_update_is_offered_when_something_waits_or_an_update_is_unfinished(self):
        self.assertTrue(f.can_update(f.parse_channel(channel(incoming=["a x"])), False))
        self.assertFalse(f.can_update(f.parse_channel(channel()), False))
        self.assertTrue(f.can_update(f.parse_channel(channel()), True))
        self.assertTrue(f.can_update(None, True))
        self.assertFalse(f.can_update(None, False))

    def test_update_is_off_while_the_channel_answer_is_on_its_way_and_on_for_an_old_controller(self):
        self.assertFalse(f.can_update(None, False, seen=False))
        self.assertTrue(f.can_update(None, False, seen=True))
        self.assertFalse(f.can_update(f.parse_channel(channel()), False, seen=True))

    def test_the_update_notice_names_what_is_waiting(self):
        ch = f.parse_channel(channel(incoming=["abc123 one", "def456 two"]))
        n = f.update_notice(ch)
        self.assertEqual(n["id"], "abc123")
        self.assertEqual(n["title"], "Update available")
        self.assertIn("2 changes waiting", n["body"])
        self.assertIn("1 change waiting", f.update_notice(f.parse_channel(channel(incoming=["abc123 one"])))["body"])

    def test_no_update_notice_when_nothing_waits_or_it_was_dismissed_or_a_job_is_running(self):
        ch = f.parse_channel(channel(incoming=["abc123 one"]))
        self.assertIsNone(f.update_notice(None))
        self.assertIsNone(f.update_notice(f.parse_channel(channel())))
        self.assertIsNone(f.update_notice(ch, dismissed="abc123"))
        self.assertIsNone(f.update_notice(ch, busy=True))
        self.assertIsNotNone(f.update_notice(ch, dismissed="older1"))

    def test_notices_with_buttons_follow_the_notification_switches(self):
        from ops_gui import notify
        self.assertIn("updates", dict(notify.SOURCES))
        self.assertTrue(notify.offer_allowed(notify.config({}), "updates"))
        self.assertFalse(notify.offer_allowed(notify.config({"notifications": {"updates": False}}), "updates"))
        self.assertFalse(notify.offer_allowed(notify.config({"notifications": {"toasts": False}}), "updates"))

    def test_the_switch_goes_to_the_other_channel(self):
        self.assertEqual(f.other_channel(f.parse_channel(channel())), {"name": "beta", "available": True})
        self.assertEqual(f.other_channel(f.parse_channel(channel("beta", "beta"))), {"name": "stable", "available": True})
        self.assertIsNone(f.other_channel(f.parse_channel(channel("my-work", None))))
        self.assertIsNone(f.other_channel(None))

    def test_a_repository_with_no_beta_says_so(self):
        ch = f.parse_channel(channel(beta=False))
        self.assertEqual(f.other_channel(ch), {"name": "beta", "available": False})
        self.assertEqual(f.switch_note(ch), "There is no beta channel yet.")
        self.assertEqual(f.switch_note(f.parse_channel(channel())), "")
        self.assertEqual(f.switch_note(f.parse_channel(channel(beta=False, error="offline"))), "")   # not known

    def test_the_question_says_what_will_happen(self):
        text = f.job_confirm_text("update", f.parse_channel(channel(incoming=["abc Fix the thing"])))
        self.assertIn("Fix the thing", text)
        self.assertIn("features you have are kept", text)
        self.assertIn("before they are well tested", f.job_confirm_text("switch", None, "beta"))
        self.assertIn("saving a crash report", f.job_confirm_text("switch", None, "beta"))
        self.assertNotIn("crash", f.job_confirm_text("switch", None, "stable"))
        self.assertIn("older", f.job_confirm_text("switch", None, "stable"))

    def test_the_commands_run_as_the_user_and_ask_only_once(self):
        self.assertEqual(f.job_argv("update"), ["aiov2_ctl", "--update", "--yes"])
        self.assertEqual(f.job_argv("switch", "beta"), ["aiov2_ctl", "--channel", "beta", "--yes"])
        # In a terminal the command asks its own question, so no --yes.
        self.assertEqual(f.job_argv("switch", "beta", terminal=True), ["x-terminal-emulator", "-e", "aiov2_ctl --channel beta"])
        self.assertEqual(f.job_argv("update", terminal=True), f.UPDATE_ARGV)
        for argv in (f.job_argv("update"), f.job_argv("switch", "beta"), f.job_argv("update", terminal=True)):
            self.assertNotIn("sudo", " ".join(argv))

    def test_root_is_known_from_either_report(self):
        ch = f.parse_channel(channel())
        self.assertEqual(ch["root"], "sudo")
        self.assertEqual(f.job_root(ch, None), "sudo")                 # pressed before the features report came
        self.assertEqual(f.job_root(None, {"root": "pkexec"}), "pkexec")
        self.assertIsNone(f.job_root(None, None))
        self.assertIsNone(f.job_root(dict(ch, root=None), {"root": None}))
        bad = json.loads(channel())
        bad["root"] = "doas"
        self.assertIsNone(f.parse_channel(json.dumps(bad))["root"])

    def test_how_it_ended(self):
        self.assertIn("Restart the app", f.job_result_text("update", "", 0))
        self.assertIn("Now on the beta channel", f.job_result_text("switch", "beta", 0))
        self.assertIn("nothing was changed", f.job_result_text("switch", "beta", 126))
        self.assertIn("could not be started", f.job_result_text("update", "", None))
        self.assertIn("exit 1", f.job_result_text("update", "", 1))

    def test_restarting_runs_this_app_again_with_the_same_python(self):
        argv = f.restart_argv("/usr/bin/python3")
        self.assertEqual(argv[-1], "/usr/bin/python3")
        self.assertIn("-m ops_gui", argv[2])


if __name__ == "__main__":
    unittest.main()

