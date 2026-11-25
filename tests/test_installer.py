"""The one installer: the feature list, choosing from it, the plan it makes and carrying the plan out. Nothing here
needs root, a network or the board: the planner is pure and the runner's commands are caught."""
import itertools
import os
import shutil
import subprocess
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiov2_ctl as a  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL_THERE = {"aio_board": True, "internal_card": True, "flipper": True, "desktop": True}
NOTHING_THERE = {k: False for k in ALL_THERE}


def state(**changes):
    base = {"user": "shane", "home": "/home/shane", "checkout": "missing", "installed": set(), "unavailable": set(),
            "sudoers_listening": False, "present": set()}
    base.update(changes)
    return base


def ids(plan):
    return [s["id"] for s in plan]


def user_entry(name="shane", uid=1000):
    return types.SimpleNamespace(pw_name=name, pw_uid=uid, pw_dir=f"/home/{name}")


class RegistryTests(unittest.TestCase):
    def test_every_feature_has_the_same_plain_fields(self):
        fields = {"key", "title", "summary", "required", "default", "apt", "hardware", "needs"}
        for f in a.FEATURES:
            self.assertEqual(set(f), fields, f["key"])
            self.assertIsInstance(f["required"], bool)
            self.assertIsInstance(f["default"], bool)
            self.assertTrue(f["title"] and f["summary"])

    def test_the_features_are_the_eleven_asked_for_in_order(self):
        self.assertEqual(a.feature_keys(), ["core", "tray", "app", "console", "gps", "mesh", "sdr", "adsb",
                                            "listening", "flipper", "autostart"])

    def test_keys_are_unique(self):
        self.assertEqual(len(a.feature_keys()), len(set(a.feature_keys())))

    def test_everything_a_feature_depends_on_exists_and_comes_before_it(self):
        seen = []
        for f in a.FEATURES:
            for need in f["needs"]:
                self.assertIn(need, seen, f"{f['key']} needs {need}")
            seen.append(f["key"])

    def test_only_the_controller_is_required(self):
        self.assertEqual([f["key"] for f in a.FEATURES if f["required"]], ["core"])

    def test_the_aircraft_receiver_and_tray_at_login_are_off_by_default(self):
        self.assertEqual([f["key"] for f in a.FEATURES if not f["default"]], ["adsb", "autostart"])

    def test_every_hardware_key_is_one_the_facts_report_and_has_words_for_when_it_is_missing(self):
        facts = a.hardware_facts(usb_ids=set(), environ={}, exists=lambda p: False, listdir=lambda p: [])
        for f in a.FEATURES:
            if f["hardware"] is not None:
                self.assertIn(f["hardware"], facts)
                self.assertIn(f["hardware"], a.HARDWARE_MISSING)
                self.assertIn(f["hardware"], a.HARDWARE_TITLES)

    def test_listening_carries_the_iw_package_install_used_to_add(self):
        for pkg in a.SYSTEM_PACKAGES:
            self.assertIn(pkg, a.feature_row("listening")["apt"])

    def test_the_app_needs_pyqt_and_the_pixel_font(self):
        self.assertIn("python3-pyqt6", a.feature_row("app")["apt"])
        self.assertIn("fonts-spleen", a.feature_row("app")["apt"])


class SelectionTests(unittest.TestCase):
    def test_no_flag_gives_the_defaults(self):
        self.assertEqual(a.select_features([], ALL_THERE),
                         ["core", "tray", "app", "console", "gps", "mesh", "sdr", "listening", "flipper"])

    def test_all_gives_every_feature(self):
        self.assertEqual(a.select_features(["--all"], ALL_THERE), a.feature_keys())

    def test_only_installs_what_is_named_plus_what_it_needs(self):
        self.assertEqual(a.select_features(["--only", "app"], ALL_THERE), ["core", "app"])
        self.assertEqual(a.select_features(["--only", "autostart"], ALL_THERE), ["core", "tray", "autostart"])
        self.assertEqual(a.select_features(["--only", "adsb,flipper"], ALL_THERE), ["core", "sdr", "adsb", "flipper"])

    def test_without_leaves_features_out_of_the_defaults(self):
        got = a.select_features(["--without", "app,console"], ALL_THERE)
        self.assertNotIn("app", got)
        self.assertNotIn("console", got)
        self.assertIn("tray", got)

    def test_leaving_a_feature_out_leaves_out_what_cannot_work_without_it(self):
        got = a.select_features(["--all", "--without", "sdr"], ALL_THERE)
        self.assertNotIn("sdr", got)
        self.assertNotIn("adsb", got)

    def test_an_unknown_feature_is_refused_with_the_valid_ones_listed(self):
        for args in (["--only", "core,nope"], ["--without", "nope"]):
            with self.assertRaises(a.InstallError) as caught:
                a.select_features(args, ALL_THERE)
            self.assertIn("nope", str(caught.exception))
            for key in a.feature_keys():
                self.assertIn(key, str(caught.exception))

    def test_the_required_feature_cannot_be_left_out(self):
        with self.assertRaises(a.InstallError):
            a.select_features(["--without", "core"], ALL_THERE)

    def test_all_and_only_together_are_refused(self):
        with self.assertRaises(a.InstallError):
            a.select_features(["--all", "--only", "tray"], ALL_THERE)

    def test_asking_for_a_feature_and_leaving_out_what_it_needs_is_refused(self):
        with self.assertRaises(a.InstallError):
            a.select_features(["--only", "autostart", "--without", "tray"], ALL_THERE)

    def test_a_list_flag_with_no_list_and_an_unknown_flag_are_refused(self):
        for args in (["--only"], ["--without"], ["--everything"], ["--only", ","]):
            with self.assertRaises(a.InstallError):
                a.parse_install_args(args)

    def test_the_flags_install_has_always_taken_still_parse(self):
        opts = a.parse_install_args(["--show-mesh-status"])
        self.assertTrue(opts["show_mesh_status"])
        opts = a.parse_install_args(["--yes", "--show-mesh-status", "--dry-run"])
        self.assertTrue(opts["yes"] and opts["dry_run"])

    def test_missing_hardware_never_removes_a_feature_from_the_selection(self):
        self.assertEqual(a.select_features([], NOTHING_THERE), a.select_features([], ALL_THERE))

    def test_dependencies_are_resolved_in_the_registry_order(self):
        self.assertEqual(a.resolve_features(["autostart", "adsb"]), ["core", "tray", "sdr", "adsb", "autostart"])
        self.assertEqual(a.resolve_features([]), ["core"])


class PlanTests(unittest.TestCase):
    def test_every_step_is_a_small_dict_with_the_agreed_fields(self):
        kinds = {"apt", "copy", "sudoers", "udev", "systemd", "service-off", "clone", "pull", "launchers",
                 "desktop-entry", "autostart", "note"}
        for step in a.install_plan(a.feature_keys(), NOTHING_THERE, state()):
            for field in ("id", "feature", "kind", "detail", "root", "undo"):
                self.assertIn(field, step)
            self.assertIn(step["kind"], kinds)
            self.assertIn(step["feature"], a.feature_keys())
            self.assertIsInstance(step["root"], bool)
            self.assertTrue(step["detail"])

    def test_step_ids_are_unique_within_a_plan(self):
        plan = a.install_plan(a.feature_keys(), NOTHING_THERE, state(unavailable={"meshtasticd", "readsb"}))
        self.assertEqual(len(ids(plan)), len(set(ids(plan))))

    def test_the_default_plan_does_everything_install_did_before_features_existed(self):
        plan = a.install_plan(a.select_features([], ALL_THERE), ALL_THERE, state())
        for step_id in a.LEGACY_INSTALL_STEPS:
            self.assertIn(step_id, ids(plan))
        # the program is in place before the rules that name it are written, as before
        self.assertLess(ids(plan).index("core.copy"), ids(plan).index("core.sudoers"))

    def test_the_default_plan_writes_the_same_password_free_rules_as_before(self):
        plan = a.install_plan(a.select_features([], ALL_THERE), ALL_THERE, state())
        sudoers = [s for s in plan if s["id"] == "core.sudoers"][0]
        self.assertTrue(sudoers["listening"])
        self.assertEqual(a.build_sudoers("shane", sudoers["listening"]), a.build_sudoers("shane"))

    def test_the_default_plan_still_installs_iw(self):
        plan = a.install_plan(a.select_features([], ALL_THERE), ALL_THERE, state())
        apt = [s for s in plan if s["id"] == "listening.apt"][0]
        self.assertEqual(set(a.SYSTEM_PACKAGES), set(apt["packages"]))

    def test_every_old_step_has_a_way_to_carry_it_out(self):
        plan = a.install_plan(a.feature_keys(), ALL_THERE, state())
        for step in plan:
            self.assertIsNotNone(a.step_action(step), step["id"])

    def test_picking_the_app_pulls_in_the_controller(self):
        plan = a.install_plan(["app"], ALL_THERE, state())
        self.assertIn("core.copy", ids(plan))
        self.assertIn("app.checkout", ids(plan))

    def test_missing_hardware_gives_a_note_and_the_feature_is_still_installed(self):
        there = a.install_plan(a.feature_keys(), ALL_THERE, state())
        absent = a.install_plan(a.feature_keys(), NOTHING_THERE, state())
        self.assertEqual([s for s in ids(there) if s.endswith(".hardware")], [])
        for step_id in ids(there):
            self.assertIn(step_id, ids(absent))
        for f in a.FEATURES:
            if f["hardware"]:
                note = [s for s in absent if s["id"] == f"{f['key']}.hardware"]
                self.assertEqual(len(note), 1, f["key"])
                self.assertEqual(note[0]["kind"], "note")
                self.assertEqual(note[0]["detail"], a.HARDWARE_MISSING[f["hardware"]])

    def test_the_note_for_a_missing_card_says_nothing_was_switched_on(self):
        self.assertIn("rail is left as it is", a.HARDWARE_MISSING["internal_card"])
        self.assertIn("0e8d:7961", a.HARDWARE_MISSING["internal_card"])
        self.assertIn("0483:5740", a.HARDWARE_MISSING["flipper"])

    def test_no_plan_ever_enables_anything_at_boot_but_the_tools_own_two_units(self):
        optional = [k for k in a.feature_keys() if k != "core"]
        for n in range(len(optional) + 1):
            for chosen in itertools.combinations(optional, n):
                for facts in (ALL_THERE, NOTHING_THERE):
                    plan = a.install_plan(list(chosen), facts, state())
                    for step in plan:
                        for unit in step.get("enables", ()):
                            self.assertIn(unit, a.OWN_BOOT_UNITS)
                        self.assertNotIn(step["kind"], ("pip", "rail", "service-on", "enable", "start"))
                        if step.get("starts_at_login"):
                            self.assertIn("autostart", chosen)
                    if "autostart" not in chosen:
                        self.assertNotIn("autostart", [s["kind"] for s in plan])

    def test_a_service_a_package_brings_is_only_ever_switched_off(self):
        plan = a.install_plan(a.feature_keys(), ALL_THERE, state())
        held = {s["feature"]: s for s in plan if s["kind"] == "service-off"}
        self.assertEqual(set(held), {"gps", "mesh", "adsb"})
        for step in held.values():
            self.assertNotIn("enables", step)
            self.assertTrue(step["disables"])

    def test_the_console_python_packages_are_left_to_its_first_run_and_never_to_pip_as_root(self):
        plan = a.install_plan(["console"], ALL_THERE, state(unavailable={"python3-textual"}))
        notes = [s["detail"] for s in plan if s["kind"] == "note"]
        self.assertTrue(any("python3-textual" in n and "first time you run it" in n for n in notes))
        self.assertTrue(any("never runs pip as root" in n for n in notes))
        for step in plan:
            if step["root"]:
                self.assertNotIn("pip", step["detail"])

    def test_a_package_that_is_not_in_the_sources_is_said_so_with_what_to_do(self):
        plan = a.install_plan(["mesh"], ALL_THERE, state(unavailable={"meshtasticd"}))
        note = [s for s in plan if s["id"] == "mesh.apt-missing-meshtasticd"][0]
        self.assertIn("--add-apps", note["detail"])

    def test_a_missing_checkout_is_cloned_and_an_existing_one_is_pulled(self):
        self.assertEqual([s["kind"] for s in a.install_plan(["app"], ALL_THERE, state()) if s["id"] == "app.checkout"],
                         ["clone"])
        plan = a.install_plan(["app"], ALL_THERE, state(checkout="ours"))
        pull = [s for s in plan if s["id"] == "app.checkout"][0]
        self.assertEqual(pull["kind"], "pull")
        self.assertIn("--ff-only", pull["detail"])
        self.assertIn("left alone", pull["detail"])

    def test_the_checkout_goes_in_the_users_home_and_its_steps_do_not_run_as_root(self):
        plan = a.install_plan(["app", "console"], ALL_THERE, state())
        for step in plan:
            if step["kind"] in ("clone", "pull", "launchers"):
                self.assertFalse(step["root"])
                self.assertEqual(step["path"], "/home/shane/ops-agentic")

    def test_the_app_and_the_console_share_one_checkout_and_one_set_of_shortcuts(self):
        plan = a.install_plan(["app", "console"], ALL_THERE, state())
        self.assertEqual([s["kind"] for s in plan].count("clone"), 1)
        self.assertEqual([s["kind"] for s in plan].count("launchers"), 1)
        clone = [s for s in plan if s["kind"] == "clone"][0]
        self.assertEqual((clone["feature"], clone["shared_with"]), ("app", ("console",)))
        alone = a.install_plan(["console"], ALL_THERE, state())
        self.assertEqual([s["feature"] for s in alone if s["kind"] == "clone"], ["console"])

    def test_something_else_in_the_checkouts_place_is_reported_and_left_alone(self):
        for kind in ("link", "other", "not-git"):
            plan = a.install_plan(["app"], ALL_THERE, state(checkout=kind))
            self.assertNotIn("clone", [s["kind"] for s in plan])
            self.assertNotIn("pull", [s["kind"] for s in plan])
            self.assertNotIn("launchers", [s["kind"] for s in plan])
            note = [s for s in plan if s["id"] == "app.checkout-blocked"][0]
            self.assertTrue(note["blocks"])
            self.assertIn("nothing in it is changed", note["detail"])

    def test_with_no_user_the_app_is_not_guessed_at_and_the_rest_is_still_planned(self):
        plan = a.install_plan(a.feature_keys(), ALL_THERE, state(user=None, home=None))
        self.assertNotIn("clone", [s["kind"] for s in plan])
        self.assertNotIn("autostart", [s["kind"] for s in plan])
        note = [s for s in plan if s["id"] == "app.no-user"][0]
        self.assertIn("SUDO_USER", note["detail"])
        self.assertIn("sudo aiov2_ctl --install", note["detail"])
        self.assertIn("core.copy", ids(plan))
        self.assertIn("flipper.udev", ids(plan))

    def test_leaving_listening_out_leaves_its_rules_out_but_never_takes_existing_ones_away(self):
        keys = a.select_features(["--without", "listening"], ALL_THERE)
        fresh = [s for s in a.install_plan(keys, ALL_THERE, state()) if s["id"] == "core.sudoers"][0]
        self.assertFalse(fresh["listening"])
        kept = [s for s in a.install_plan(keys, ALL_THERE, state(sudoers_listening=True)) if s["id"] == "core.sudoers"][0]
        self.assertTrue(kept["listening"])

    def test_rules_without_listening_have_no_listening_commands_and_keep_the_rest(self):
        text = a.build_sudoers("shane", listening=False)
        for word in ("--wifi-listen", "--wifi-survey", "--bt-scan"):
            self.assertNotIn(word, text)
        self.assertIn("--boot-rail USB on", text)
        self.assertNotIn("*", text.replace("# ", ""))

    def test_steps_that_can_be_taken_back_say_how(self):
        plan = a.install_plan(a.feature_keys(), ALL_THERE, state())
        for step in plan:
            if step["kind"] in ("copy", "sudoers", "udev", "systemd", "desktop-entry", "autostart", "apt"):
                self.assertTrue(step["undo"], step["id"])

    def test_the_plan_reads_in_plain_words_and_marks_what_runs_as_the_user(self):
        keys = a.feature_keys()
        text = a.format_plan(a.install_plan(keys, NOTHING_THERE, state()), keys)
        self.assertIn("This will install: " + ", ".join(keys), text)
        self.assertIn("[as you, not root]", text)
        self.assertIn("note: " + a.HARDWARE_MISSING["flipper"], text)
        self.assertIn("No rail is switched on", text)


class FactsTests(unittest.TestCase):
    def facts(self, usb=(), env=None, files=(), sessions=()):
        return a.hardware_facts(usb_ids=set(usb), environ=env or {}, exists=lambda p: p in files,
                                listdir=lambda p: list(sessions))

    def test_an_empty_machine_has_nothing(self):
        self.assertEqual(self.facts(), NOTHING_THERE)

    def test_the_internal_card_is_known_by_its_usb_id_and_means_the_board_is_there(self):
        got = self.facts(usb=[("0e8d", "7961")])
        self.assertTrue(got["internal_card"])
        self.assertTrue(got["aio_board"])
        self.assertFalse(got["flipper"])

    def test_a_flipper_is_known_by_its_usb_id_and_is_not_the_board(self):
        got = self.facts(usb=[("0483", "5740")])
        self.assertTrue(got["flipper"])
        self.assertFalse(got["aio_board"])

    def test_the_board_is_also_seen_by_its_tuner_its_package_or_the_lora_port(self):
        self.assertTrue(self.facts(usb=[("0bda", "2838")])["aio_board"])
        self.assertTrue(self.facts(files=[a.AIO_BOARD_PACKAGE_DOC])["aio_board"])
        self.assertTrue(self.facts(files=["/dev/spidev1.0"])["aio_board"])

    def test_a_desktop_is_a_running_session_or_an_installed_one(self):
        self.assertTrue(self.facts(env={"DISPLAY": ":0"})["desktop"])
        self.assertTrue(self.facts(sessions=["labwc.desktop"])["desktop"])
        self.assertFalse(self.facts(sessions=["README"])["desktop"])

    def test_usb_ids_are_read_from_sysfs_and_interfaces_are_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            for name, vid, pid in (("1-1", "0E8D", "7961"), ("1-1:1.0", "dead", "beef")):
                os.mkdir(os.path.join(base, name))
                for leaf, value in (("idVendor", vid), ("idProduct", pid)):
                    with open(os.path.join(base, name, leaf), "w") as f:
                        f.write(value + "\n")
            self.assertEqual(a.usb_ids_present(base), {("0e8d", "7961")})
        self.assertEqual(a.usb_ids_present("/nonexistent/usb"), set())


class InvokingUserTests(unittest.TestCase):
    def who(self, env, euid):
        names = {"shane": user_entry(), "root": user_entry("root", 0), "toor": user_entry("toor", 0)}

        def by_name(name):
            return names[name]
        return a.invoking_user(environ=env, euid=euid, by_name=by_name, by_uid=lambda uid: user_entry("me", uid))

    def test_under_sudo_it_is_the_user_who_ran_sudo(self):
        self.assertEqual(self.who({"SUDO_USER": "shane"}, 0).pw_name, "shane")

    def test_plain_root_with_no_sudo_user_is_not_guessed(self):
        self.assertIsNone(self.who({}, 0))
        self.assertIsNone(self.who({"USER": "shane", "LOGNAME": "shane", "HOME": "/home/shane"}, 0))

    def test_root_named_as_the_sudo_user_or_another_name_for_root_is_refused(self):
        self.assertIsNone(self.who({"SUDO_USER": "root"}, 0))
        self.assertIsNone(self.who({"SUDO_USER": "toor"}, 0))

    def test_a_user_that_does_not_exist_or_a_name_that_is_not_a_name_is_refused(self):
        self.assertIsNone(self.who({"SUDO_USER": "ghost"}, 0))
        self.assertIsNone(self.who({"SUDO_USER": "shane; rm -rf /"}, 0))
        self.assertIsNone(self.who({"SUDO_USER": "-u"}, 0))

    def test_without_root_it_is_whoever_is_running_it(self):
        self.assertEqual(self.who({"SUDO_USER": "someone"}, 1000).pw_uid, 1000)

    def test_the_message_for_plain_root_says_how_to_run_it(self):
        self.assertIn("sudo aiov2_ctl --install", a.NO_USER_MESSAGE)

    def test_root_runs_a_users_command_through_sudo_as_that_user(self):
        self.assertEqual(a.as_user("shane", ["git", "status"], euid=0), ["sudo", "-u", "shane", "-H", "--", "git", "status"])
        self.assertEqual(a.as_user("shane", ["git", "status"], euid=1000), ["git", "status"])
        self.assertEqual(a.as_user("shane", ["git", "x"], euid=0, env={"GIT_TERMINAL_PROMPT": "0"}),
                         ["sudo", "-u", "shane", "-H", "--", "env", "GIT_TERMINAL_PROMPT=0", "git", "x"])


class CheckoutTests(unittest.TestCase):
    PATH = "/home/shane/ops-agentic"

    def runner(self, answers):
        """A stand-in for running commands: `answers` maps a word in the command to (code, out, err)."""
        calls = []

        def run(cmd, cwd=None, timeout=None):
            calls.append(cmd)
            for word, answer in answers.items():
                if word in cmd:
                    return answer
            return 0, "", ""
        return run, calls

    def test_the_repository_is_recognised_however_its_address_is_spelled(self):
        for url in ("https://github.com/AceMedia/uConsole-AIO-OPS.git", "https://github.com/AceMedia/uConsole-AIO-OPS",
                    "git@github.com:AceMedia/uConsole-AIO-OPS.git", "ssh://git@github.com/acemedia/uconsole-aio-ops\n"):
            self.assertTrue(a.is_ops_remote(url), url)
        for url in ("", None, "https://github.com/someone/uConsole-AIO-OPS.git",
                    "https://evil.example/AceMedia/uConsole-AIO-OPS.git",
                    "https://github.com/AceMedia/uConsole-AIO-OPS-fake"):
            self.assertFalse(a.is_ops_remote(url), url)

    def test_what_is_in_the_checkouts_place_is_told_apart_without_following_a_link(self):
        import stat as st_mod
        folder = types.SimpleNamespace(st_mode=st_mod.S_IFDIR | 0o755)
        link = types.SimpleNamespace(st_mode=st_mod.S_IFLNK | 0o777)
        plain = types.SimpleNamespace(st_mode=st_mod.S_IFREG | 0o644)

        def missing(path):
            raise FileNotFoundError(path)
        run, calls = self.runner({"remote": (0, "git@github.com:AceMedia/uConsole-AIO-OPS.git\n", "")})
        self.assertEqual(a.checkout_state(self.PATH, "shane", run, lstat=missing, euid=0), "missing")
        self.assertEqual(a.checkout_state(self.PATH, "shane", run, lstat=lambda p: link, euid=0), "link")
        self.assertEqual(a.checkout_state(self.PATH, "shane", run, lstat=lambda p: plain, euid=0), "not-git")
        self.assertEqual(calls, [])          # git is not even asked about a link
        self.assertEqual(a.checkout_state(self.PATH, "shane", run, lstat=lambda p: folder, euid=0), "ours")
        self.assertEqual(calls[0][:5], ["sudo", "-u", "shane", "-H", "--"])
        run, _ = self.runner({"remote": (0, "https://github.com/else/thing.git\n", "")})
        self.assertEqual(a.checkout_state(self.PATH, "shane", run, lstat=lambda p: folder, euid=0), "other")
        run, _ = self.runner({"remote": (128, "", "not a git repository")})
        self.assertEqual(a.checkout_state(self.PATH, "shane", run, lstat=lambda p: folder, euid=0), "not-git")

    def test_a_clone_refused_for_lack_of_access_says_so_and_what_to_do(self):
        for err in ("remote: Repository not found.\nfatal: repository 'https://github.com/...' not found",
                    "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
                    "fatal: Authentication failed for 'https://github.com/AceMedia/uConsole-AIO-OPS.git/'",
                    "git@github.com: Permission denied (publickey)."):
            text = a.clone_failure_message(err, self.PATH)
            self.assertIn("private", text)
            self.assertIn("gh auth login", text)
            self.assertIn(self.PATH, text)
            self.assertIn("run the install again", text)

    def test_a_clone_that_failed_for_another_reason_does_not_blame_access(self):
        text = a.clone_failure_message("fatal: unable to access '...': Could not resolve host: github.com", self.PATH)
        self.assertNotIn("private", text)
        self.assertIn("Could not resolve host", text)
        self.assertIn(self.PATH, text)

    def step(self, kind):
        return {"id": "app.checkout", "feature": "app", "kind": kind, "path": self.PATH}

    def act(self, kind, place, answers):
        run, calls = self.runner(answers)
        with mock.patch.object(a, "checkout_state", return_value=place), \
                mock.patch.object(a.os, "geteuid", return_value=0):
            result = a._act_checkout(self.step(kind), {"user": "shane", "home": "/home/shane"}, run)
        return result, calls

    def test_cloning_runs_git_as_the_user_and_never_asks_for_a_password(self):
        (status, _), calls = self.act("clone", "missing", {})
        self.assertEqual(status, "done")
        self.assertEqual(calls, [["sudo", "-u", "shane", "-H", "--", "env", "GIT_TERMINAL_PROMPT=0", "git", "clone",
                                  a.OPS_REPO_URL, self.PATH]])

    def test_a_refused_clone_fails_that_step_with_the_plain_message(self):
        (status, words), _ = self.act("clone", "missing", {"clone": (128, "", "remote: Repository not found.")})
        self.assertEqual(status, "failed")
        self.assertIn("gh auth login", words)

    def test_a_clean_checkout_is_fast_forwarded_as_the_user(self):
        (status, _), calls = self.act("pull", "ours", {"pull": (0, "Updating 1..2\nFast-forward\n", "")})
        self.assertEqual(status, "done")
        self.assertIn("--ff-only", calls[-1])
        for cmd in calls:
            self.assertEqual(cmd[:5], ["sudo", "-u", "shane", "-H", "--"])

    def test_a_checkout_that_is_up_to_date_counts_as_already_there(self):
        (status, _), _ = self.act("pull", "ours", {"pull": (0, "Already up to date.\n", "")})
        self.assertEqual(status, "already")

    def test_a_checkout_with_local_changes_is_reported_and_not_pulled(self):
        (status, words), calls = self.act("pull", "ours", {"status": (0, " M ops.py\n", "")})
        self.assertEqual(status, "already")
        self.assertIn("changes of your own", words)
        self.assertFalse(any("pull" in cmd for cmd in calls))

    def test_a_checkout_that_cannot_fast_forward_is_reported_and_never_reset(self):
        (status, words), calls = self.act("pull", "ours", {"pull": (128, "", "fatal: Not possible to fast-forward")})
        self.assertEqual(status, "already")
        self.assertIn("left as it is", words)
        for cmd in calls:
            for word in ("reset", "checkout", "clean", "stash", "--force", "-f", "rebase", "merge"):
                self.assertNotIn(word, cmd)

    def test_something_else_put_there_since_the_plan_was_made_is_left_alone(self):
        (status, words), calls = self.act("clone", "link", {})
        self.assertEqual(status, "failed")
        self.assertIn("left alone", words)
        self.assertEqual(calls, [])

    def test_with_no_user_git_is_not_run_at_all(self):
        run, calls = self.runner({})
        status, words = a._act_checkout(self.step("clone"), {"user": None}, run)
        self.assertEqual((status, words), ("failed", a.NO_USER_MESSAGE))
        self.assertEqual(calls, [])

    def test_the_shortcuts_are_written_by_the_apps_own_command_as_the_user_from_the_checkout(self):
        seen = []

        def run(cmd, cwd=None, timeout=None):
            seen.append((cmd, cwd))
            return 0, "", ""
        with mock.patch.object(a, "checkout_state", return_value="ours"), \
                mock.patch.object(a.os, "geteuid", return_value=0):
            status, _ = a._act_launchers({"id": "app.launchers", "feature": "app", "kind": "launchers", "path": self.PATH},
                                         {"user": "shane", "home": "/nonexistent/home"}, run)
        self.assertEqual(status, "done")
        self.assertEqual(seen, [(["sudo", "-u", "shane", "-H", "--", "/usr/bin/python3", "-m", "ops_gui",
                                  "--install-launchers"], self.PATH)])

    def test_no_shortcuts_are_written_when_the_checkout_is_not_there(self):
        run, calls = self.runner({})
        with mock.patch.object(a, "checkout_state", return_value="missing"):
            status, _ = a._act_launchers({"id": "app.launchers", "feature": "app", "kind": "launchers", "path": self.PATH},
                                         {"user": "shane", "home": "/nonexistent/home"}, run)
        self.assertEqual(status, "failed")
        self.assertEqual(calls, [])


class StateTests(unittest.TestCase):
    def test_the_version_apt_would_install_is_read_and_none_means_not_available(self):
        self.assertEqual(a.apt_candidate("gpsd:\n  Installed: (none)\n  Candidate: 3.22-4.1\n"), "3.22-4.1")
        self.assertIsNone(a.apt_candidate("meshtasticd:\n  Installed: (none)\n  Candidate: (none)\n"))
        self.assertIsNone(a.apt_candidate(""))

    def test_a_feature_looks_installed_when_all_its_parts_are_there(self):
        self.assertEqual(a.feature_installed("gps", state()), "no")
        self.assertEqual(a.feature_installed("gps", state(installed={"gpsd"})), "yes")
        self.assertEqual(a.feature_installed("app", state(installed={"git"})), "partly")
        self.assertEqual(a.feature_installed("app", state(installed={"git", "python3-pyqt6", "fonts-spleen"},
                                                          present={"checkout", "gui-shortcut"})), "yes")
        self.assertEqual(a.feature_installed("flipper", state(present={"udev"})), "yes")

    def test_the_console_counts_as_installed_without_its_optional_packages(self):
        self.assertEqual(a.feature_installed("console", state(installed={"git"},
                                                              present={"checkout", "console-shortcut"})), "yes")

    def test_the_feature_table_lists_every_feature_with_its_default_and_needs(self):
        text = a.format_features(state(installed={"gpsd"}), NOTHING_THERE)
        for f in a.FEATURES:
            self.assertIn(f["key"], text)
            self.assertIn(f["title"], text)
        rows = {line.split()[0]: line.split() for line in text.splitlines() if line.split()[:1] and
                line.split()[0] in a.feature_keys()}
        self.assertEqual(rows["core"][1:3], ["always", "no"])
        self.assertEqual(rows["adsb"][1], "off")
        self.assertEqual(rows["gps"][1:3], ["on", "yes"])
        self.assertIn("(not found now)", text)
        self.assertIn("packages git, python3-pyqt6, fonts-spleen", text)


class RunTests(unittest.TestCase):
    def test_one_step_failing_does_not_stop_the_others(self):
        plan = a.install_plan(["tray", "flipper"], ALL_THERE, state())
        done = []

        def action_for(step):
            def act(step, ctx):
                done.append(step["id"])
                if step["id"] == "tray.assets":
                    raise OSError("disk full")
                return "done", ""
            return act
        outcomes = a.run_install(plan, {}, say=lambda *_: None, action_for=action_for)
        self.assertEqual(done, ids(plan))
        rows = dict((k, (s, w)) for k, s, w in a.feature_results(["core", "tray", "flipper"], outcomes))
        self.assertEqual(rows["core"][0], "installed")
        self.assertEqual(rows["flipper"][0], "installed")
        self.assertEqual(rows["tray"][0], "failed")
        self.assertIn("disk full", rows["tray"][1])

    def test_a_feature_where_nothing_needed_changing_is_already_there(self):
        plan = a.install_plan(["flipper"], ALL_THERE, state())
        outcomes = [(s, "already", "") for s in plan]
        self.assertEqual(a.feature_results(["core", "flipper"], outcomes),
                         [("core", "already there", ""), ("flipper", "already there", "")])

    def test_a_failed_checkout_fails_both_the_app_and_the_console_and_nothing_else(self):
        plan = a.install_plan(["app", "console", "flipper"], ALL_THERE, state())
        outcomes = [(s, "failed" if s["kind"] == "clone" else "done", "no access" if s["kind"] == "clone" else "")
                    for s in plan]
        rows = {k: s for k, s, _ in a.feature_results(["core", "app", "console", "flipper"], outcomes)}
        self.assertEqual(rows, {"core": "installed", "app": "failed", "console": "failed", "flipper": "installed"})
        text = a.format_results(a.feature_results(["core", "app", "console", "flipper"], outcomes))
        self.assertIn("failed: no access", text)

    def test_with_no_user_the_app_fails_with_how_to_run_it_and_the_rest_carries_on(self):
        plan = a.install_plan(["app", "flipper"], ALL_THERE, state(user=None, home=None))

        def action_for(step):
            return a._act_note if step["kind"] == "note" else (lambda step, ctx: ("done", ""))
        outcomes = a.run_install(plan, {}, say=lambda *_: None, action_for=action_for)
        rows = {k: (s, w) for k, s, w in a.feature_results(["core", "app", "flipper"], outcomes)}
        self.assertEqual(rows["app"], ("failed", a.NO_USER_MESSAGE))
        self.assertEqual(rows["flipper"][0], "installed")

    def test_a_step_this_version_cannot_do_is_a_failure_not_a_silent_skip(self):
        outcomes = a.run_install([{"id": "x.y", "feature": "core", "kind": "mystery", "detail": "?", "root": True,
                                   "undo": ""}], {}, say=lambda *_: None)
        self.assertEqual(outcomes[0][1], "failed")

    def test_carrying_out_the_whole_plan_never_switches_a_rail_starts_a_service_or_runs_pip(self):
        """Every command the real actions would run for --all, with nothing really run."""
        commands = []

        def call(cmd, *args, **kwargs):
            commands.append(list(cmd))
            return 0

        def run(cmd, *args, **kwargs):
            commands.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        plan = a.install_plan(a.feature_keys(), ALL_THERE, state())
        ctx = {"user": "shane", "home": "/nonexistent/home", "source": ("/nonexistent/aiov2_ctl.py", None, {}),
               "added": set()}
        with mock.patch.object(a.subprocess, "call", side_effect=call), \
                mock.patch.object(a.subprocess, "check_call", side_effect=call), \
                mock.patch.object(a.subprocess, "run", side_effect=run), \
                mock.patch.object(a, "package_installed", return_value=False), \
                mock.patch.object(a, "package_available", return_value=True), \
                mock.patch.object(a, "_write_system_file", return_value=True), \
                mock.patch.object(a, "ensure_sudo_path_link"), \
                mock.patch.object(a, "ensure_companion_udev_rules"), \
                mock.patch.object(a, "setup_sudoers", return_value=0), \
                mock.patch.object(a, "load_config", return_value={}), \
                mock.patch.object(a, "checkout_state", side_effect=["missing", "ours"]), \
                mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.object(a.GpioController, "set_feature") as set_feature, \
                mock.patch.object(a.GpioController, "set_gpio") as set_gpio:
            outcomes = a.run_install(plan, ctx, say=lambda *_: None)
        self.assertEqual([s["id"] for s, status, _ in outcomes if status == "failed"], [])
        set_feature.assert_not_called()
        set_gpio.assert_not_called()
        self.assertTrue(commands)
        enabled = []
        for cmd in commands:
            self.assertNotIn("pip", cmd)
            self.assertNotIn("pip3", cmd)
            self.assertNotIn("pipx", cmd)
            if cmd[0] == "systemctl":
                self.assertIn(cmd[1], ("daemon-reload", "enable", "disable"), cmd)
                if cmd[1] == "enable":
                    enabled += [word for word in cmd[2:] if not word.startswith("-")]
            # anything in the user's home is done as the user
            if any(word in cmd for word in ("clone", "ops_gui", "--autostart")):
                self.assertEqual(cmd[:5], ["sudo", "-u", "shane", "-H", "--"], cmd)
        self.assertEqual(sorted(set(enabled)), sorted(a.OWN_BOOT_UNITS))
        # the services the packages brought were switched back off, since this run added them
        off = [cmd for cmd in commands if cmd[:3] == ["systemctl", "disable", "--now"]]
        self.assertEqual(sorted(unit for cmd in off for unit in cmd[3:]),
                         ["gpsd.service", "gpsd.socket", "meshtasticd.service", "readsb.service"])

    def test_a_service_that_was_already_installed_is_left_exactly_as_it_is(self):
        step = [s for s in a.install_plan(["gps"], ALL_THERE, state()) if s["kind"] == "service-off"][0]
        with mock.patch.object(a.subprocess, "call") as call:
            self.assertEqual(a._act_service_off(step, {"added": set()})[0], "already")
        call.assert_not_called()

    def test_a_mesh_the_owner_set_to_start_at_boot_is_kept(self):
        step = [s for s in a.install_plan(["mesh"], ALL_THERE, state()) if s["kind"] == "service-off"][0]
        with mock.patch.object(a.subprocess, "call") as call, \
                mock.patch.object(a, "load_config", return_value={"mesh_on_boot": True}):
            a._act_service_off(step, {"added": {"meshtasticd"}})
        call.assert_not_called()

    def test_a_package_missing_from_the_sources_fails_its_feature_unless_it_is_optional(self):
        def installed(pkg, run=None):
            return pkg == "git"
        with mock.patch.object(a, "package_installed", side_effect=installed), \
                mock.patch.object(a, "package_available", return_value=False), \
                mock.patch.object(a.subprocess, "call", return_value=0) as call:
            mesh = [s for s in a.install_plan(["mesh"], ALL_THERE, state()) if s["kind"] == "apt"][0]
            status, words = a._act_apt(mesh, {"apt_updated": True})
            self.assertEqual(status, "failed")
            self.assertIn("--add-apps", words)
            console = [s for s in a.install_plan(["console"], ALL_THERE, state()) if s["kind"] == "apt"][0]
            status, words = a._act_apt(console, {"apt_updated": True})
            self.assertEqual(status, "note")
            self.assertIn("first run", words)
        call.assert_not_called()

    def test_the_rules_step_passes_the_listening_choice_on(self):
        with mock.patch.object(a, "setup_sudoers", return_value=0) as setup:
            a._act_core_sudoers({"listening": False}, {})
        setup.assert_called_once_with(listening=False)


class CommandTests(unittest.TestCase):
    def run_command(self, args, euid=0, isatty=True, answer="y"):
        said, asked, ran = [], [], []

        def execute(plan, ctx, say):
            ran.append(plan)
            return [(s, "done", "") for s in plan]

        def ask(prompt):
            asked.append(prompt)
            return answer
        code = a.install_command(args, facts=ALL_THERE, state=state(), execute=execute, ask=ask, isatty=isatty,
                                 euid=euid, say=said.append)
        return code, "\n".join(said), asked, ran

    def test_a_dry_run_prints_the_plan_changes_nothing_and_needs_no_root(self):
        with mock.patch.object(a.subprocess, "call", side_effect=AssertionError("ran something")), \
                mock.patch.object(a.subprocess, "check_call", side_effect=AssertionError("ran something")), \
                mock.patch.object(a.subprocess, "run", side_effect=AssertionError("ran something")), \
                mock.patch("builtins.open", side_effect=AssertionError("opened something")):
            code, text, asked, ran = self.run_command(["--dry-run", "--all"], euid=1000, isatty=False)
        self.assertEqual((code, asked, ran), (0, [], []))
        self.assertIn("This will install: " + ", ".join(a.feature_keys()), text)
        self.assertIn("Dry run: nothing was changed.", text)

    def test_it_asks_once_and_a_no_changes_nothing(self):
        code, text, asked, ran = self.run_command([], answer="n")
        self.assertEqual((code, len(asked), ran), (0, 1, []))
        self.assertIn("Nothing was changed.", text)

    def test_it_asks_once_and_a_yes_runs_the_plan_and_ends_with_a_table(self):
        code, text, asked, ran = self.run_command([])
        self.assertEqual((code, len(asked), len(ran)), (0, 1, 1))
        self.assertIn("Result", text)
        for key in a.select_features([], ALL_THERE):
            self.assertRegex(text, rf"(?m)^  {key}\s+installed")
        self.assertIn("Install complete.", text)

    def test_yes_on_the_command_line_means_it_does_not_ask(self):
        code, _, asked, ran = self.run_command(["--yes"], isatty=False)
        self.assertEqual((code, asked, len(ran)), (0, [], 1))

    def test_with_no_terminal_it_never_asks_and_wants_yes_or_dry_run(self):
        code, text, asked, ran = self.run_command([], isatty=False)
        self.assertEqual((code, asked, ran), (1, [], []))
        self.assertIn("--yes", text)
        self.assertIn("--dry-run", text)

    def test_without_root_a_real_install_is_refused_as_before(self):
        code, text, asked, ran = self.run_command(["--yes"], euid=1000)
        self.assertEqual((code, asked, ran), (1, [], []))
        self.assertIn("Install requires sudo.", text)
        self.assertIn("Run: sudo aiov2_ctl --install", text)

    def test_a_bad_selection_is_refused_before_anything_else_happens(self):
        code, text, asked, ran = self.run_command(["--only", "nope", "--yes"])
        self.assertEqual((code, asked, ran), (2, [], []))
        self.assertIn("Valid features:", text)

    def test_a_failed_feature_is_named_at_the_end_and_the_exit_code_says_so(self):
        said = []

        def execute(plan, ctx, say):
            return [(s, "failed" if s["kind"] == "clone" else "done", "no access") for s in plan]
        code = a.install_command(["--yes"], facts=ALL_THERE, state=state(), execute=execute, isatty=False, euid=0,
                                 say=said.append)
        text = "\n".join(said)
        self.assertEqual(code, 1)
        self.assertRegex(text, r"(?m)^  app\s+failed: no access")
        self.assertRegex(text, r"(?m)^  core\s+installed")
        self.assertIn("not everything went in: app, console", text)

    def test_the_mesh_status_is_still_shown_when_asked_for(self):
        with mock.patch.object(a, "print_mesh_on_boot_status_hint") as hint:
            code, _, _, _ = self.run_command(["--yes", "--show-mesh-status"])
        self.assertEqual(code, 0)
        hint.assert_called_once()

    def test_the_update_starts_the_install_without_a_second_question(self):
        import inspect
        self.assertIn('["--install", "--yes", "--show-mesh-status"]', inspect.getsource(a.update_self))

    def test_install_and_features_are_commands(self):
        with mock.patch.object(sys, "argv", ["aiov2_ctl", "--install", "--dry-run", "--only", "flipper"]), \
                mock.patch.object(a, "install_command", return_value=0) as cmd, self.assertRaises(SystemExit) as done:
            a.main()
        self.assertEqual(done.exception.code, 0)
        cmd.assert_called_once_with(["--dry-run", "--only", "flipper"])
        with mock.patch.object(sys, "argv", ["aiov2_ctl", "--features"]), \
                mock.patch.object(a, "features_command", return_value=0) as cmd, self.assertRaises(SystemExit):
            a.main()
        cmd.assert_called_once_with()
        self.assertIn("--features", a.HELP_TEXT)
        self.assertIn("--features", a.BASH_COMPLETION)


class BootstrapScriptTests(unittest.TestCase):
    PATH = os.path.join(REPO, "install.sh")

    def text(self):
        with open(self.PATH) as f:
            return f.read()

    @unittest.skipUnless(shutil.which("sh"), "no sh here")
    def test_the_script_is_valid_shell(self):
        self.assertEqual(subprocess.call(["sh", "-n", self.PATH]), 0)

    def test_it_stops_on_errors_checks_the_download_and_cleans_up(self):
        text = self.text()
        self.assertIn("set -eu", text)
        self.assertIn("python3 -m py_compile", text)
        self.assertIn("mktemp -d", text)
        self.assertIn("trap 'rm -rf \"$tmp\"' EXIT", text)
        self.assertLess(text.index("py_compile"), text.index("sudo python3 \"$file\""))

    def test_it_downloads_only_this_repositorys_file_and_pipes_nothing_into_a_shell(self):
        text = self.text()
        self.assertIn("https://raw.githubusercontent.com/AceMedia/aiov2_ctl/main/aiov2_ctl.py", text)
        self.assertEqual(text.count("https://"), 1)
        for line in text.splitlines():
            if not line.lstrip().startswith("#"):
                self.assertNotRegex(line, r"\|\s*(sudo\s+)?(sh|bash)\b")

    def test_it_installs_everything_when_given_no_arguments_and_says_so_before_sudo(self):
        text = self.text()
        self.assertIn('[ "$#" -gt 0 ] || set -- --all', text)
        self.assertLess(text.index("About to run as root"), text.index("\nsudo python3"))


class SourceCheckoutTests(unittest.TestCase):
    """Which copy of the program an install puts in place."""

    def test_a_file_in_a_checkout_installs_from_that_checkout(self):
        self.assertEqual(a.source_checkout("/home/u/repo/aiov2_ctl.py", "/usr/local/bin/aiov2_ctl",
                                           "/home/u/repo", "/home/u/old"), "/home/u/repo")

    def test_a_loose_file_installs_itself_not_an_older_recorded_checkout(self):
        self.assertIsNone(a.source_checkout("/tmp/dl/aiov2_ctl.py", "/usr/local/bin/aiov2_ctl", None, "/home/u/old"))

    def test_the_installed_copy_run_by_itself_uses_the_recorded_checkout(self):
        self.assertEqual(a.source_checkout("/usr/local/bin/aiov2_ctl", "/usr/local/bin/aiov2_ctl", None,
                                           "/home/u/old"), "/home/u/old")

    def test_the_installed_copy_with_nothing_recorded_installs_itself(self):
        self.assertIsNone(a.source_checkout("/usr/local/bin/aiov2_ctl", "/usr/local/bin/aiov2_ctl", None, None))


if __name__ == "__main__":
    unittest.main()
