import json
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


_REAL_META_PATH = a.INSTALL_META_PATH


def setUpModule():
    # No test here may read this machine's own install record: what it says would change what a plain install
    # selects. Tests that want a record pass one in.
    a.INSTALL_META_PATH = "/nonexistent/aiov2_ctl/install.json"


def tearDownModule():
    a.INSTALL_META_PATH = _REAL_META_PATH


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

    def test_the_apps_folder_moves_to_the_controllers_channel_before_it_is_pulled(self):
        run, calls = self.runner({"ls-remote": (0, "a\trefs/heads/main\nb\trefs/heads/beta\n", ""),
                                  "pull": (0, "Already up to date.\n", "")})
        ctx = {"user": "shane", "home": "/home/shane", "source": ("/x/aiov2_ctl.py", None, {"branch": "beta"})}
        with mock.patch.object(a, "checkout_state", return_value="ours"), \
                mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch("builtins.open", mock.mock_open(read_data="ref: refs/heads/main\n")):
            a._act_checkout(self.step("pull"), ctx, run)
        words = [" ".join(c) for c in calls]
        self.assertTrue(any("checkout --quiet beta" in w for w in words))
        self.assertLess(next(i for i, w in enumerate(words) if "checkout --quiet beta" in w),
                        next(i for i, w in enumerate(words) if "pull --ff-only" in w))
        for cmd in calls:
            self.assertEqual(cmd[:5], ["sudo", "-u", "shane", "-H", "--"])

    def test_the_apps_folder_stays_put_when_the_controller_is_on_the_same_channel(self):
        run, calls = self.runner({"pull": (0, "Already up to date.\n", "")})
        ctx = {"user": "shane", "home": "/home/shane", "source": ("/x/aiov2_ctl.py", None, {"branch": "main"})}
        with mock.patch.object(a, "checkout_state", return_value="ours"), \
                mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch("builtins.open", mock.mock_open(read_data="ref: refs/heads/main\n")):
            a._act_checkout(self.step("pull"), ctx, run)
        self.assertFalse(any("ls-remote" in c or "checkout" in c for c in calls))

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
        self.assertIn("finish_update(two_steps=True)", inspect.getsource(a.update_self))
        for cmd in a.update_install_steps("/usr/bin/python3", "/usr/local/bin/aiov2_ctl"):
            self.assertIn("--yes", cmd)

    def test_install_and_features_are_commands(self):
        with mock.patch.object(sys, "argv", ["aiov2_ctl", "--install", "--dry-run", "--only", "flipper"]), \
                mock.patch.object(a, "install_command", return_value=0) as cmd, self.assertRaises(SystemExit) as done:
            a.main()
        self.assertEqual(done.exception.code, 0)
        cmd.assert_called_once_with(["--dry-run", "--only", "flipper"])
        with mock.patch.object(sys, "argv", ["aiov2_ctl", "--features"]), \
                mock.patch.object(a, "features_command", return_value=0) as cmd, self.assertRaises(SystemExit):
            a.main()
        cmd.assert_called_once_with([])
        self.assertIn("--features", a.HELP_TEXT)
        self.assertIn("--features", a.BASH_COMPLETION)


class BootstrapScriptTests(unittest.TestCase):
    PATH = os.path.join(REPO, "install.sh")

    def text(self):
        with open(self.PATH) as f:
            return f.read()

    def repo(self):
        """The repository the script says it belongs to."""
        import re
        found = re.findall(r'^REPO="([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"$', self.text(), re.M)
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_the_repository_is_named_once_and_the_readme_names_the_same_one(self):
        text, repo = self.text(), self.repo()
        self.assertEqual(text.count(repo), 1)
        with open(os.path.join(REPO, "README.md")) as f:
            readme = f.read()
        self.assertIn(f"curl -fsSLO https://raw.githubusercontent.com/{repo}/main/install.sh", readme)
        self.assertEqual(readme.count("raw.githubusercontent.com"), 1)

    @unittest.skipUnless(shutil.which("sh"), "no sh here")
    def test_the_script_is_valid_shell(self):
        self.assertEqual(subprocess.call(["sh", "-n", self.PATH]), 0)

    def test_it_stops_on_errors_checks_the_download_and_cleans_up(self):
        text = self.text()
        self.assertIn("set -eu", text)
        self.assertIn("py_compile.compile(", text)
        self.assertIn("mktemp -d", text)
        self.assertIn("trap 'rm -rf \"$tmp\"' EXIT", text)
        self.assertLess(text.index("py_compile"), text.index("sudo python3 \"$file\""))

    def test_it_downloads_only_this_repositorys_file_and_pipes_nothing_into_a_shell(self):
        text = self.text()
        self.assertIn('URL="https://raw.githubusercontent.com/$REPO/main/aiov2_ctl.py"', text)
        self.assertIn('REPO_URL="https://github.com/$REPO.git"', text)
        self.assertEqual(text.count("https://"), 2)
        for line in text.splitlines():
            if not line.lstrip().startswith("#"):
                self.assertNotRegex(line, r"\|\s*(sudo\s+)?(sh|bash)\b")

    def test_it_installs_everything_when_given_no_arguments_and_says_so_before_sudo(self):
        text = self.text()
        self.assertIn('[ "$#" -gt 0 ] || set -- --all', text)
        self.assertLess(text.index("About to run as root"), text.index("\nsudo python3"))

    def checkout(self, base, name, origin):
        """A stand-in checkout whose program only prints what it was asked."""
        path = os.path.join(base, name)
        os.makedirs(path)
        with open(os.path.join(path, "aiov2_ctl.py"), "w") as f:
            f.write("import sys\nprint('STUB', ' '.join(sys.argv[1:]))\n")
        subprocess.check_call(["git", "init", "-q", path])
        subprocess.check_call(["git", "-C", path, "remote", "add", "origin", origin])
        return path

    def dry_run(self, base, folder, record="none.json"):
        env = dict(os.environ, HOME=base, AIOV2_DIR=folder, AIOV2_RECORD=os.path.join(base, record))
        return subprocess.run(["sh", self.PATH, "--dry-run"], env=env, capture_output=True, text=True, timeout=60)

    @unittest.skipUnless(shutil.which("git") and os.path.exists("/etc/os-release") and os.geteuid() != 0,
                         "needs git, a Debian-like system and an ordinary user")
    def test_a_dry_run_uses_a_checkout_of_this_repository_and_leaves_it_untouched(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            path = self.checkout(base, "mine", f"https://github.com/{self.repo()}.git")
            before = sorted(os.listdir(path))
            done = self.dry_run(base, path)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("STUB --install --dry-run", done.stdout)
            self.assertNotIn("Downloading", done.stdout)
            self.assertEqual(sorted(os.listdir(path)), before)          # no compiled copy left in it either

    @unittest.skipUnless(shutil.which("git") and os.path.exists("/etc/os-release") and os.geteuid() != 0,
                         "needs git, a Debian-like system and an ordinary user")
    def test_the_checkout_an_earlier_install_recorded_is_preferred(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            path = self.checkout(base, "recorded", f"git@github.com:{self.repo()}.git")
            with open(os.path.join(base, "rec.json"), "w") as f:
                json.dump({"repo_path": path}, f)
            done = self.dry_run(base, os.path.join(base, "nothing-here"), record="rec.json")
            self.assertIn(f"using the checkout in {path}", done.stdout)
            self.assertIn("STUB --install --dry-run", done.stdout)

    @unittest.skipUnless(shutil.which("git") and os.path.exists("/etc/os-release") and os.geteuid() != 0,
                         "needs git, a Debian-like system and an ordinary user")
    def test_the_checkout_the_readme_always_named_is_found(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            path = self.checkout(base, "aiov2_ctl", f"https://github.com/{self.repo()}")
            done = self.dry_run(base, os.path.join(base, "nothing-here"))
            self.assertIn(f"using the checkout in {path}", done.stdout)
            self.assertIn("STUB --install --dry-run", done.stdout)

    @unittest.skipUnless(shutil.which("git") and os.path.exists("/etc/os-release") and os.geteuid() != 0,
                         "needs git, a Debian-like system and an ordinary user")
    def test_a_checkout_of_another_repository_is_not_used_even_with_a_similar_name(self):
        import tempfile
        owner, name = self.repo().split("/")
        for origin in (f"https://github.com/someone-else/{name}.git", f"https://github.com/{owner}/{name}-fake.git",
                       f"https://evil.example/{owner}/{name}.git"):
            with tempfile.TemporaryDirectory() as base:
                path = self.checkout(base, "aiov2_ctl", origin)
                # A stand-in curl, so the download the script falls back to never touches the network.
                os.makedirs(os.path.join(base, "bin"))
                curl = os.path.join(base, "bin", "curl")
                with open(curl, "w") as f:
                    f.write('#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                            'echo "print(\'DOWNLOADED\')" > "$out"\n')
                os.chmod(curl, 0o755)
                env = dict(os.environ, HOME=base, AIOV2_DIR=path, AIOV2_RECORD=os.path.join(base, "none.json"),
                           PATH=os.path.join(base, "bin") + os.pathsep + os.environ["PATH"])
                done = subprocess.run(["sh", self.PATH, "--dry-run"], env=env, capture_output=True, text=True,
                                      timeout=60)
                self.assertNotIn("using the checkout", done.stdout, origin)
                self.assertNotIn("STUB", done.stdout, origin)
                self.assertIn("DOWNLOADED", done.stdout, origin)

    def test_a_folder_that_is_some_other_project_is_never_pulled_or_run(self):
        text = self.text()
        self.assertIn('git -C "$1" remote get-url origin 2>/dev/null | grep -Eqi', text)
        self.assertIn("is not a checkout of this tool, so it was left alone", text)
        self.assertNotIn("git reset", text)
        self.assertNotIn("rm -rf \"$DIR\"", text)


class OpsIncomingTests(unittest.TestCase):
    """An update also offers changes that are only in the ops app."""

    def runner(self, url="https://github.com/AceMedia/uConsole-AIO-OPS.git", branch="beta", fetch=0,
               log="abc123 one\ndef456 two\n"):
        calls = []

        def run(cmd, cwd=None, timeout=None):
            calls.append(cmd)
            if "get-url" in cmd:
                return 0, url + "\n", ""
            if "rev-parse" in cmd:
                return 0, branch + "\n", ""
            if "fetch" in cmd:
                return fetch, "", ""
            if "log" in cmd:
                return 0, log, ""
            return 1, "", ""
        return run, calls

    def look(self, **kw):
        run, calls = self.runner(**kw)
        return a.ops_incoming("/home/u/ops-agentic", run=run, isdir=lambda p: True), calls

    def test_it_lists_what_waits_on_the_folders_own_branch(self):
        got, calls = self.look()
        self.assertEqual(got, ["abc123 one (ops app)", "def456 two (ops app)"])
        self.assertIn("HEAD..origin/beta", calls[-1])
        self.assertTrue(all(c[:2] == ["env", "GIT_TERMINAL_PROMPT=0"] for c in calls))

    def test_it_changes_no_file(self):
        _, calls = self.look()
        for c in calls:
            self.assertFalse({"pull", "merge", "checkout", "reset"} & set(c), c)

    def test_nothing_when_there_is_no_checkout_or_it_is_another_repository(self):
        run, calls = self.runner()
        self.assertEqual(a.ops_incoming("/home/u/ops-agentic", run=run, isdir=lambda p: False), [])
        self.assertEqual(calls, [])
        got, calls = self.look(url="https://github.com/someone/else.git")
        self.assertEqual(got, [])
        self.assertFalse(any("fetch" in c for c in calls))

    def test_nothing_when_it_cannot_be_reached_or_is_on_no_branch_or_is_up_to_date(self):
        self.assertEqual(self.look(fetch=1)[0], [])
        self.assertEqual(self.look(branch="HEAD")[0], [])
        self.assertEqual(self.look(log="")[0], [])


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


class PickerTests(unittest.TestCase):
    """The feature picker: the pure model, what it shows, and how --install --pick uses it."""

    def test_it_starts_from_the_usual_set(self):
        self.assertEqual(a.FeaturePicker().selected(), a.default_features())

    def test_ticking_a_feature_ticks_what_it_needs_and_says_so(self):
        p = a.FeaturePicker(["core"])
        p.toggle("adsb")
        self.assertEqual(p.selected(), ["core", "sdr", "adsb"])
        self.assertIn("Software radio", p.message)

    def test_unticking_a_feature_unticks_what_cannot_work_without_it(self):
        p = a.FeaturePicker(a.feature_keys())
        p.toggle("tray")
        self.assertNotIn("tray", p.ticked)
        self.assertNotIn("autostart", p.ticked)
        self.assertIn("Tray at login", p.message)
        p.toggle("sdr")
        self.assertNotIn("adsb", p.ticked)

    def test_a_required_feature_stays_ticked(self):
        p = a.FeaturePicker()
        p.toggle("core")
        self.assertIn("core", p.ticked)
        self.assertIn("cannot be left out", p.message)
        p.press("n")
        self.assertEqual(p.selected(), ["core"])

    def test_every_selection_the_picker_can_reach_is_one_the_installer_accepts(self):
        for presses in itertools.product(range(len(a.FEATURES)), repeat=3):
            p = a.FeaturePicker()
            for i in presses:
                p.toggle(a.feature_keys()[i])
            chosen = p.selected()
            self.assertEqual(chosen, a.resolve_features(chosen), presses)

    def test_keys_move_tick_and_finish(self):
        p = a.FeaturePicker(["core"])
        p.press("up")
        self.assertEqual(p.cursor, 0)
        p.press("down")
        self.assertEqual(p.current(), "tray")
        p.press("space")
        self.assertIn("tray", p.ticked)
        p.press("end")
        self.assertEqual(p.current(), a.feature_keys()[-1])
        p.press("down")
        self.assertEqual(p.current(), a.feature_keys()[-1])
        p.press("a")
        self.assertEqual(p.selected(), a.feature_keys())
        p.press("u")
        self.assertEqual(p.selected(), a.default_features())
        self.assertIsNone(p.press("?"))
        self.assertEqual(p.press("enter"), "accept")
        self.assertEqual(p.press("q"), "cancel")

    def test_the_rows_show_ticks_what_is_installed_and_missing_hardware(self):
        p = a.FeaturePicker(["core", "flipper"])
        st = state(installed={"gpsd"}, present=set())
        lines = a.picker_lines(p, st, NOTHING_THERE)
        texts = [t for t, _ in lines]
        flipper = next(t for t in texts if "Flipper and Marauder" in t)
        self.assertIn("[x]", flipper)
        self.assertIn("a Flipper Zero not found", flipper)
        gps = next(t for t in texts if t.lstrip().startswith("[ ] GPS"))
        self.assertIn("installed", gps)
        self.assertEqual([c for _, c in lines].count(True), 1)
        self.assertTrue(any("does not remove it" in t for t in texts))

    def test_nothing_is_said_about_removing_when_nothing_installed_is_unticked(self):
        texts = [t for t, _ in a.picker_lines(a.FeaturePicker(), state(), ALL_THERE)]
        self.assertFalse(any("does not remove it" in t for t in texts))

    def test_the_cursor_row_is_explained_underneath(self):
        p = a.FeaturePicker()
        p.cursor = a.feature_keys().index("adsb")
        texts = [t for t, _ in a.picker_lines(p, state(), ALL_THERE)]
        self.assertIn(a.feature_row("adsb")["summary"], texts)
        self.assertIn("Needs: packages readsb; Software radio", texts)

    def test_wrapping_keeps_every_word_and_the_cursor_row(self):
        lines = a.picker_lines(a.FeaturePicker(), state(), NOTHING_THERE)
        wrapped = a.wrap_for_screen(lines, 40)
        self.assertTrue(all(len(t) < 40 for t, _ in wrapped))
        self.assertEqual(" ".join(" ".join(t.split()) for t, _ in wrapped if t),
                         " ".join(" ".join(t.split()) for t, _ in lines if t))
        self.assertTrue(any(c for _, c in wrapped))
        row = next(i for i, (t, _) in enumerate(wrapped) if "Passive Wi-Fi" in t)
        self.assertTrue(wrapped[row + 1][0].startswith("     "))        # carried on under the title

    def test_the_plain_line_picker_takes_numbers_names_and_letters(self):
        answers = iter(["8", "tray 99", "", ])
        said = []
        p = a.FeaturePicker(["core", "tray"])
        chosen = a.pick_by_lines(p, state(), ALL_THERE, ask=lambda _: next(answers), say=said.append)
        self.assertEqual(chosen, ["core", "sdr", "adsb"])
        self.assertTrue(any("'99' is not one of" in s for s in said))
        self.assertTrue(any(" 8. [x] Aircraft receiver" in s for s in said))

    def test_the_plain_line_picker_can_be_quit(self):
        self.assertIsNone(a.pick_by_lines(a.FeaturePicker(), state(), ALL_THERE, ask=lambda _: "q", say=lambda _: 0))

        def gone(_):
            raise EOFError
        self.assertIsNone(a.pick_by_lines(a.FeaturePicker(), state(), ALL_THERE, ask=gone, say=lambda _: 0))

    def test_a_terminal_that_cannot_do_a_screen_gets_the_plain_lines(self):
        def broken(*_):
            raise RuntimeError("no screen")
        for env, screen in (({"TERM": "dumb"}, lambda *_: self.fail("screen used on a dumb terminal")),
                            ({"TERM": "xterm"}, broken)):
            chosen = a.pick_features(a.FeaturePicker(["core"]), state(), ALL_THERE, ask=lambda _: "",
                                     say=lambda _: 0, screen=screen, environ=env)
            self.assertEqual(chosen, ["core"])

    def run_pick(self, args, picked, isatty=True, euid=0):
        said, ran, seen = [], [], []

        def execute(plan, ctx, say):
            ran.append(ids(plan))
            return [(s, "done", "") for s in plan]

        def pick(picker, st, facts):
            seen.append(picker.selected())
            return picked

        code = a.install_command(args, facts=ALL_THERE, state=state(), execute=execute, ask=lambda _: "y",
                                 isatty=isatty, euid=euid, say=said.append, pick=pick)
        return code, "\n".join(said), ran, seen

    def test_pick_installs_what_was_ticked(self):
        code, said, ran, seen = self.run_pick(["--pick"], ["core", "flipper"])
        self.assertEqual(code, 0)
        self.assertEqual(seen, [a.default_features()])
        self.assertIn("This will install: core, flipper", said)
        self.assertEqual(len(ran), 1)
        self.assertNotIn("tray.assets", ran[0])

    def test_pick_starts_from_what_the_other_flags_chose(self):
        _, _, _, seen = self.run_pick(["--pick", "--only", "adsb"], ["core"])
        self.assertEqual(seen, [["core", "sdr", "adsb"]])

    def test_quitting_the_picker_changes_nothing(self):
        code, said, ran, _ = self.run_pick(["--pick"], None)
        self.assertEqual((code, ran), (0, []))
        self.assertIn("nothing was changed", said)

    def test_pick_needs_a_terminal_even_with_yes_or_dry_run(self):
        for args in (["--pick", "--yes"], ["--pick", "--dry-run"]):
            code, said, ran, seen = self.run_pick(args, ["core"], isatty=False)
            self.assertEqual((code, ran, seen), (1, [], []))
            self.assertIn("--only LIST", said)

    def test_pick_with_dry_run_needs_no_root_and_changes_nothing(self):
        code, said, ran, _ = self.run_pick(["--pick", "--dry-run"], ["core", "gps"], euid=1000)
        self.assertEqual((code, ran), (0, []))
        self.assertIn("This will install: core, gps", said)

    def test_pick_is_in_the_help_and_completion(self):
        self.assertIn("--pick", a.HELP_TEXT)
        self.assertIn("--pick", a.BASH_COMPLETION)
        self.assertIn("--pick", a.INSTALL_USAGE)


EVERYTHING_PRESENT = {"exe", "completion", "rails-unit", "desktop-entry", "checkout", "gui-shortcut",
                      "console-shortcut", "sdr-recovery", "sudoers-listening", "udev", "autostart"}


class RepairTests(unittest.TestCase):
    def test_a_machine_with_only_ordinary_packages_has_nothing_installed(self):
        st = state(installed={"git", "iw", "gpsd", "python3-pyqt6"})
        self.assertEqual(a.features_in_place(st), [])
        self.assertEqual(a.repair_selection(st), [])

    def test_what_is_in_place_is_found_by_what_this_tool_wrote(self):
        st = state(installed={"gpsd", "git"}, present={"exe", "udev", "checkout", "gui-shortcut"})
        self.assertEqual(a.features_in_place(st), ["core", "app", "gps", "flipper"])

    def test_the_shared_folder_alone_does_not_count_for_the_console(self):
        st = state(present={"exe", "checkout", "gui-shortcut"})
        self.assertNotIn("console", a.features_in_place(st))

    def test_the_record_wins_over_what_is_in_place(self):
        st = state(present=set(EVERYTHING_PRESENT))
        self.assertEqual(a.recorded_features({"features": ["tray", "core", "nonsense"]}, st), ["core", "tray"])
        self.assertEqual(a.repair_selection(st, {"features": ["adsb"]}), ["core", "sdr", "adsb"])
        self.assertEqual(a.repair_selection(st, {"features": []}), [])

    def test_missing_pieces_are_named(self):
        st = state(installed={"python3-pyqt6"}, present={"exe", "completion"})
        self.assertEqual(a.feature_missing("core", st), ["the rail boot service"])
        self.assertEqual(a.feature_missing("tray", st), ["the tray's start-menu entry"])
        self.assertEqual(a.feature_missing("gps", st), ["the package gpsd"])
        text = a.format_repair(["core", "tray"], st)
        self.assertIn("core       missing: the rail boot service", text)
        every = state(installed=set(a.all_feature_packages()), present=set(EVERYTHING_PRESENT))
        self.assertIn("core       looks complete", a.format_repair(["core"], every))

    def test_every_marker_has_words(self):
        for markers in a.FEATURE_MARKERS.values():
            for m in markers:
                self.assertIn(m, a.MARKER_WORDS)

    def run_repair(self, args, known, st=None, isatty=True, euid=0):
        said, ran, ctxs = [], [], []

        def execute(plan, ctx, say):
            ran.append(ids(plan))
            ctxs.append(ctx)
            return [(s, "done", "") for s in plan]

        code = a.install_command(args, facts=ALL_THERE, state=st or state(), execute=execute, ask=lambda _: "y",
                                 isatty=isatty, euid=euid, say=said.append, known=known)
        return code, "\n".join(said), ran, ctxs

    def test_repair_reinstalls_what_was_installed_and_nothing_else(self):
        code, said, ran, ctxs = self.run_repair(["--repair"], ["core", "tray"])
        self.assertEqual(code, 0)
        self.assertIn("This will install: core, tray", said)
        self.assertIn("missing:", said)
        self.assertEqual(ctxs[0]["features"], ["core", "tray"])
        self.assertFalse(any(i.startswith(("gps.", "app.", "sdr.")) for i in ran[0]))

    def test_repair_with_nothing_installed_does_nothing(self):
        code, said, ran, _ = self.run_repair(["--repair"], [])
        self.assertEqual((code, ran), (0, []))
        self.assertIn("nothing to repair", said)

    def test_repair_takes_no_choice_of_features(self):
        for extra in (["--all"], ["--only", "core"], ["--without", "tray"], ["--pick"]):
            code, said, ran, _ = self.run_repair(["--repair", *extra], ["core"])
            self.assertEqual((code, ran), (2, []), extra)

    def test_repair_dry_run_needs_no_root(self):
        code, said, ran, _ = self.run_repair(["--repair", "--dry-run"], ["core"], euid=1000)
        self.assertEqual((code, ran), (0, []))
        self.assertIn("Dry run", said)

    def test_an_install_records_what_was_there_before_as_well(self):
        _, _, _, ctxs = self.run_repair(["--only", "flipper", "--yes"], ["core", "tray"])
        self.assertEqual(ctxs[0]["features"], ["core", "tray", "flipper"])

    def test_the_first_recorded_install_keeps_what_was_already_in_place(self):
        st = state(present={"exe", "desktop-entry"})
        said, ctxs = [], []
        with mock.patch.object(a, "load_install_meta", return_value={"repo_path": "/x"}):
            a.install_command(["--only", "flipper", "--yes"], facts=ALL_THERE, state=st,
                              execute=lambda plan, ctx, say: ctxs.append(ctx) or [], euid=0, say=said.append)
        self.assertEqual(ctxs[0]["features"], ["core", "tray", "flipper"])


class UninstallTests(unittest.TestCase):
    def st(self, **changes):
        return state(installed=set(a.all_feature_packages()), present=set(EVERYTHING_PRESENT), checkout="ours",
                     **changes)

    def test_everything_is_removed_last_installed_first(self):
        sel = a.uninstall_selection()
        self.assertEqual(sel, list(reversed(a.feature_keys())))
        self.assertEqual(sel[-1], "core")

    def test_removing_a_feature_removes_what_needs_it(self):
        self.assertEqual(a.uninstall_selection(["sdr"]), ["adsb", "sdr"])
        self.assertEqual(a.uninstall_selection(["tray"]), ["autostart", "tray"])
        self.assertEqual(a.uninstall_selection(["core"]), list(reversed(a.feature_keys())))
        self.assertEqual(a.uninstall_selection(["flipper"]), ["flipper"])

    def test_bad_arguments_are_refused(self):
        for args in (["--only"], ["--only", "nope"], ["--all"], ["--purge"]):
            with self.assertRaises(a.InstallError):
                a.parse_uninstall_args(args)

    def test_no_step_touches_a_package_a_rail_or_a_radio_service(self):
        plan = a.uninstall_plan(a.uninstall_selection(), self.st())
        for step in plan:
            self.assertNotIn(step["kind"], ("apt",))
            for unit in step.get("units", ()):
                self.assertIn(unit, (a.RAILS_BOOT_SERVICE, a.SDR_WATCHDOG_TIMER))
            for path in step.get("paths", ()):
                self.assertFalse(path.startswith(("/usr/bin", "/usr/lib", "/boot", "/dev", "/sys")), path)
        text = a.format_uninstall_plan(plan, a.uninstall_selection())
        self.assertIn("No package is removed", text)
        self.assertIn("No rail is switched", text)
        self.assertNotIn("apt remove", text)

    def test_only_this_tools_own_files_are_named(self):
        plan = a.uninstall_plan(a.uninstall_selection(), self.st())
        paths = [p for s in plan for p in s.get("paths", ()) + s.get("trees", ())]
        allowed = ("/etc/sudoers.d/aiov2_ctl", "/etc/systemd/system/aiov2-", "/etc/systemd/system/readsb.service.d/10-aiov2-",
                   "/etc/bash_completion.d/aiov2_ctl", "/usr/local/share/aiov2_ctl/", "/usr/share/applications/aiov2_ctl.desktop",
                   "/usr/share/applications/aiov2_ctl-installer.desktop", a.INSTALL_META_PATH,
                   "/home/shane/.config/autostart/aiov2_ctl.desktop", "/home/shane/.local/share/applications/uconsole-ops")
        for p in paths:
            self.assertTrue(p.startswith(allowed), p)
        self.assertNotIn(a.CONFIG_PATH, paths)                          # saved rail settings are left

    def test_the_app_folder_goes_only_when_both_users_of_it_go(self):
        both = a.uninstall_plan(a.uninstall_selection(), self.st())
        self.assertEqual([s["id"] for s in both if s["kind"] == "checkout"], ["un.app.checkout"])
        self.assertLess(ids(both).index("un.console.shortcut"), ids(both).index("un.app.checkout"))
        one = a.uninstall_plan(["console"], self.st())
        self.assertEqual(ids(one), ["un.console.shortcut", "un.console.checkout-kept"])
        self.assertIn("stays", one[1]["detail"])

    def test_listening_alone_rewrites_the_rules_and_with_core_they_just_go(self):
        self.assertEqual(ids(a.uninstall_plan(["listening"], self.st())), ["un.listening.sudoers"])
        full = ids(a.uninstall_plan(a.uninstall_selection(), self.st()))
        self.assertNotIn("un.listening.sudoers", full)
        self.assertIn("un.core.sudoers", full)

    def test_the_program_is_the_very_last_thing_removed(self):
        self.assertEqual(ids(a.uninstall_plan(a.uninstall_selection(), self.st()))[-1], "un.core.exe")

    def test_without_a_user_nothing_in_a_home_is_guessed_at(self):
        plan = a.uninstall_plan(["autostart", "console", "app"], state(user=None, home=None))
        self.assertTrue(all(s["kind"] == "note" and s.get("blocks") for s in plan))

    def runner(self, answers):
        def run(cmd, cwd=None, timeout=None):
            runner.calls.append(cmd)
            for needle, result in answers:
                if needle in " ".join(cmd):
                    return result
            return 0, "", ""
        runner = types.SimpleNamespace(calls=[], run=run)
        return runner

    def test_a_checkout_with_anything_of_yours_is_left(self):
        for needle, out, why in (("status --porcelain", " M app.py\n", "changed or added"),
                                 ("stash list", "stash@{0}: WIP\n", "stashed"),
                                 ("--not --remotes", "abc123 mine\n", "never pushed")):
            r = self.runner([("remote get-url", (0, a.OPS_REPO_URL, "")), (needle, (0, out, ""))])
            ok, said = a.checkout_holds_nothing("/home/shane/ops-agentic", "shane", r.run)
            self.assertFalse(ok)
            self.assertIn(why, said)
            self.assertFalse(any("rm" in c for c in r.calls))

    def test_a_clean_checkout_holds_nothing(self):
        r = self.runner([])
        self.assertEqual(a.checkout_holds_nothing("/home/shane/ops-agentic", "shane", r.run), (True, ""))

    def test_the_checkout_step_refuses_any_other_folder(self):
        r = self.runner([])
        status, words = a._un_checkout({"path": "/home/shane"}, {"user": "shane", "home": "/home/shane"}, run=r.run)
        self.assertEqual(status, "failed")
        self.assertEqual(r.calls, [])

    def test_the_checkout_step_leaves_a_dirty_folder_and_removes_a_clean_one(self):
        ctx = {"user": "shane", "home": "/home/shane"}
        step = {"path": "/home/shane/ops-agentic"}
        with mock.patch.object(a, "checkout_state", return_value="ours"):
            dirty = self.runner([("status --porcelain", (0, "?? notes.txt\n", ""))])
            self.assertEqual(a._un_checkout(step, ctx, run=dirty.run)[0], "note")
            self.assertFalse(any("rm" in c for c in dirty.calls))
            clean = self.runner([])
            self.assertEqual(a._un_checkout(step, ctx, run=clean.run), ("done", ""))
            self.assertEqual(clean.calls[-1][-4:], ["rm", "-rf", "--", "/home/shane/ops-agentic"])
        with mock.patch.object(a, "checkout_state", return_value="other"):
            other = self.runner([])
            self.assertEqual(a._un_checkout(step, ctx, run=other.run)[0], "note")
            self.assertEqual(other.calls, [])

    def test_results_say_what_happened_to_each_feature(self):
        sel = ["flipper", "gps", "app", "tray"]
        plan = a.uninstall_plan(sel, self.st())
        by = {"un.flipper.udev": ("done", ""), "un.gps.kept": ("note", ""), "un.app.shortcut": ("already", ""),
              "un.app.checkout-kept": ("note", ""), "un.tray.desktop-entry": ("failed", "busy")}
        rows = dict((k, s) for k, s, _ in a.uninstall_results(sel, [(s, *by[s["id"]]) for s in plan]))
        self.assertEqual(rows, {"flipper": "removed", "gps": "nothing to remove", "app": "was not there",
                                "tray": "failed"})

    def run_un(self, args, isatty=True, euid=0, answer="y", fail=()):
        said, ran, forgot = [], [], []

        def execute(plan, ctx, say):
            ran.append(ids(plan))
            return [(s, "failed" if s["feature"] in fail else ("note" if s["kind"] == "note" else "done"), "")
                    for s in plan]

        code = a.uninstall_command(args, state=self.st(), execute=execute, ask=lambda _: answer, isatty=isatty,
                                   euid=euid, say=said.append, forget=lambda keys, st: forgot.append(keys))
        return code, "\n".join(said), ran, forgot

    def test_it_asks_first_and_no_means_no(self):
        code, said, ran, forgot = self.run_un([], answer="n")
        self.assertEqual((code, ran, forgot), (0, [], []))
        self.assertIn("Nothing was changed", said)

    def test_it_needs_root_a_terminal_or_yes(self):
        self.assertEqual(self.run_un([], euid=1000)[0], 1)
        self.assertEqual(self.run_un([], isatty=False)[0::2], (1, []))
        self.assertEqual(len(self.run_un(["--yes"], isatty=False)[2]), 1)

    def test_dry_run_changes_nothing_and_needs_no_root(self):
        code, said, ran, forgot = self.run_un(["--dry-run"], euid=1000, isatty=False)
        self.assertEqual((code, ran, forgot), (0, [], []))
        self.assertIn("This will remove: autostart", said)

    def test_what_was_removed_is_taken_off_the_record_but_not_what_failed(self):
        code, said, ran, forgot = self.run_un(["--only", "sdr", "--yes"], fail=("sdr",))
        self.assertEqual(code, 1)
        self.assertEqual(forgot, [["adsb"]])
        code, _, _, forgot = self.run_un(["--only", "flipper", "--yes"])
        self.assertEqual((code, forgot), (0, [["flipper"]]))

    def test_a_full_uninstall_keeps_no_record(self):
        code, said, ran, forgot = self.run_un(["--yes"])
        self.assertEqual((code, forgot), (0, []))
        self.assertNotIn("Put anything back", said)

    def test_bad_arguments_exit_2(self):
        self.assertEqual(self.run_un(["--only", "nope"])[0], 2)

    def test_they_are_in_the_help_and_completion(self):
        for word in ("--repair", "--uninstall"):
            self.assertIn(word, a.HELP_TEXT)
            self.assertIn(word, a.BASH_COMPLETION)

    def test_real_file_actions_remove_only_what_is_there(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            one, tree = os.path.join(base, "one"), os.path.join(base, "img")
            open(one, "w").close()
            os.mkdir(tree)
            open(os.path.join(tree, "x.png"), "w").close()
            step = {"paths": (one, os.path.join(base, "never-there")), "trees": (tree,)}
            self.assertEqual(a._un_files(step, {}), ("done", ""))
            self.assertEqual(os.listdir(base), [])
            self.assertEqual(a._un_files(step, {}), ("already", ""))

    def test_a_udev_rule_someone_changed_is_left(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            rule = os.path.join(base, "99.rules")
            with open(rule, "w") as f:
                f.write("# mine\n")
            with mock.patch.object(a, "COMPANION_UDEV_PATH", rule), mock.patch.object(a.subprocess, "call") as call:
                self.assertEqual(a._un_udev({}, {})[0], "note")
                self.assertTrue(os.path.exists(rule))
                with open(rule, "w") as f:
                    f.write(a.COMPANION_UDEV_RULES)
                self.assertEqual(a._un_udev({}, {}), ("done", ""))
                self.assertFalse(os.path.exists(rule))
                self.assertEqual(a._un_udev({}, {}), ("already", ""))


class InstallerWindowHelperTests(unittest.TestCase):
    """What the installer window decides. The window itself is only ever checked on a real screen."""

    def test_root_comes_from_sudo_when_it_needs_no_password_else_the_desktop_asks(self):
        self.assertEqual(a.elevation(True, "/usr/bin/pkexec"), "sudo")
        self.assertEqual(a.elevation(False, "/usr/bin/pkexec"), "pkexec")
        self.assertIsNone(a.elevation(False, None))

    def test_the_command_installs_exactly_what_was_ticked_and_never_asks(self):
        argv = a.installer_argv("sudo", "/x/aiov2_ctl.py", "install", ["core", "tray"])
        self.assertEqual(argv, ["sudo", "-n", "/usr/bin/python3", "-u", "/x/aiov2_ctl.py", "--apply", "--keep",
                                "core,tray", "--yes"])
        opts = a.parse_apply_args(argv[6:])
        self.assertEqual((opts["keep"], opts["yes"], opts["dry_run"]), (["core", "tray"], True, False))
        self.assertEqual(a.installer_argv("pkexec", "/x/a.py", "repair")[:1] + a.installer_argv("pkexec", "/x/a.py", "repair")[-2:],
                         ["pkexec", "--repair", "--yes"])

    def test_every_selection_the_window_can_make_is_one_the_command_line_accepts(self):
        for keys in (["core"], a.default_features(), a.feature_keys(), ["core", "sdr", "adsb"]):
            p = a.FeaturePicker(keys)
            opts = a.parse_apply_args(a.installer_arguments("install", p.selected())[1:])
            self.assertEqual(a.apply_changes(opts["keep"], [])["install"], p.selected())
        a.parse_install_args(a.installer_arguments("repair"))

    def test_the_command_shown_for_a_terminal_can_be_typed_as_it_is(self):
        self.assertEqual(a.installer_command_text("install", ["core", "gps"]), "sudo aiov2_ctl --apply --keep core,gps")
        self.assertEqual(a.installer_command_text("repair"), "sudo aiov2_ctl --repair")

    def test_rows_carry_ticks_fixed_boxes_and_notes(self):
        st = state(installed={"gpsd"}, present={"exe", "completion", "rails-unit"})
        rows = {r["key"]: r for r in a.installer_rows(a.FeaturePicker(["core", "gps"]), st, NOTHING_THERE)}
        self.assertEqual(len(rows), len(a.FEATURES))
        self.assertTrue(rows["core"]["fixed"] and rows["core"]["ticked"])
        self.assertEqual(rows["core"]["installed"], "yes")
        self.assertFalse(rows["tray"]["ticked"] or rows["tray"]["fixed"])
        self.assertIn("a Flipper Zero not found", rows["flipper"]["note"])
        self.assertIn("Needs Software radio.", rows["adsb"]["summary"])
        self.assertNotIn("Needs", rows["tray"]["summary"])              # needing only the controller goes unsaid

    def test_the_summary_says_what_is_new(self):
        st = state(present={"exe", "completion", "rails-unit"})
        self.assertIn("all already installed", a.installer_summary(a.FeaturePicker(["core"]), st))
        text = a.installer_summary(a.FeaturePicker(["core", "flipper"]), st)
        self.assertIn("2 ticked, 1 to add or finish: Flipper and Marauder.", text)

    def test_the_last_line_tells_done_from_refused_from_failed(self):
        self.assertEqual(a.installer_finished_text("install", 0), "Install complete.")
        self.assertEqual(a.installer_finished_text("repair", 0), "Repair complete.")
        self.assertIn("Nothing was changed", a.installer_finished_text("install", 126))
        self.assertIn("Nothing was changed", a.installer_finished_text("install", 127))
        self.assertIn("not everything went in", a.installer_finished_text("install", 1))

    def test_a_window_needs_a_display(self):
        self.assertTrue(a.desktop_running({"DISPLAY": ":0"}))
        self.assertTrue(a.desktop_running({"WAYLAND_DISPLAY": "wayland-0"}))
        self.assertFalse(a.desktop_running({}))

    def test_the_window_refuses_root_and_a_machine_with_no_display(self):
        with mock.patch.object(a.os, "geteuid", return_value=0), mock.patch("builtins.print") as said:
            self.assertEqual(a.run_installer(), 1)
            self.assertIn("as yourself", said.call_args_list[0][0][0])
        with mock.patch.object(a.os, "geteuid", return_value=1000), mock.patch.dict(a.os.environ, {}, clear=True), \
                mock.patch("builtins.print") as said:
            self.assertEqual(a.run_installer(), 1)
            self.assertIn("--install --pick", said.call_args_list[0][0][0])

    def test_an_install_started_by_pkexec_knows_whose_home_to_use(self):
        got = a.invoking_user({"PKEXEC_UID": "1000"}, 0, by_uid=lambda uid: user_entry("shane", uid))
        self.assertEqual(got.pw_name, "shane")
        self.assertIsNone(a.invoking_user({"PKEXEC_UID": "0"}, 0, by_uid=lambda uid: user_entry("root", 0)))
        self.assertIsNone(a.invoking_user({"PKEXEC_UID": "abc"}, 0))
        # sudo's name still wins when both are there
        got = a.invoking_user({"SUDO_USER": "shane", "PKEXEC_UID": "1001"}, 0, by_name=lambda n: user_entry(n))
        self.assertEqual(got.pw_name, "shane")

    def test_it_is_in_the_help_and_completion(self):
        self.assertIn("--installer", a.HELP_TEXT)
        self.assertIn("--installer", a.BASH_COMPLETION)



class FeaturesReportTests(unittest.TestCase):
    """aiov2_ctl --features --json: what the ops app's Settings reads."""

    def report(self, listed, **changes):
        out = a.features_report(state(**changes), {"features": listed}, {"desktop": True, "aio_board": False}, "sudo")
        return out, {r["key"]: r for r in out["features"]}

    def test_it_lists_every_feature_in_order_and_survives_json(self):
        out, rows = self.report(["core"])
        self.assertEqual([r["key"] for r in out["features"]], a.feature_keys())
        self.assertEqual(json.loads(json.dumps(out)), out)
        self.assertEqual((out["version"], out["root"], out["unfinished"]), (1, "sudo", False))
        self.assertIs(a.features_report(state(), {}, {}, None, unfinished=True)["unfinished"], True)

    def test_installed_is_what_the_installer_recorded(self):
        _, rows = self.report(["core", "tray", "sdr"])
        self.assertEqual([k for k, r in rows.items() if r["installed"]], ["core", "tray", "sdr"])
        self.assertTrue(rows["core"]["required"])
        self.assertFalse(rows["tray"]["required"])

    def test_removing_takes_only_the_installed_things_that_need_it(self):
        _, rows = self.report(["core", "tray", "sdr", "autostart"])
        self.assertEqual(rows["tray"]["removes"], ["autostart"])
        self.assertEqual(rows["sdr"]["removes"], [])          # the aircraft receiver needs it but is not installed
        _, rows = self.report(["core", "sdr", "adsb"])
        self.assertEqual(rows["sdr"]["removes"], ["adsb"])

    def test_adding_brings_what_it_needs_and_does_not_have(self):
        _, rows = self.report(["core"])
        self.assertEqual(rows["adsb"]["adds"], ["sdr"])
        self.assertEqual(rows["autostart"]["adds"], ["tray"])
        _, rows = self.report(["core", "sdr"])
        self.assertEqual(rows["adsb"]["adds"], [])

    def test_what_is_missing_is_said_only_for_installed_features(self):
        _, rows = self.report(["core", "tray"])
        self.assertTrue(rows["tray"]["missing"])               # nothing of the tray is in this bare state
        self.assertEqual(rows["sdr"]["missing"], [])

    def test_hardware_is_named_and_only_true_or_false_when_known(self):
        _, rows = self.report(["core"])
        self.assertIs(rows["tray"]["hardware_found"], True)
        self.assertIs(rows["sdr"]["hardware_found"], False)
        self.assertIsNone(rows["console"]["hardware_found"])
        self.assertEqual(rows["console"]["hardware"], "")
        self.assertTrue(rows["sdr"]["hardware"])

    def test_the_command_rejects_anything_but_json(self):
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(a.features_command(["--yaml"]), 2)
        self.assertIn("--json", out.getvalue())


class InstallerEntryTests(unittest.TestCase):
    """The installer window's start-menu entry comes and goes with the tray."""

    def test_the_tray_writes_both_entries(self):
        plan = a.install_plan(["core", "tray"], NOTHING_THERE, state())
        self.assertIn("tray.installer-entry", ids(plan))
        self.assertLess(ids(plan).index("tray.desktop-entry"), ids(plan).index("tray.installer-entry"))
        self.assertNotIn("tray.installer-entry", ids(a.install_plan(["core"], NOTHING_THERE, state())))

    def test_every_planned_step_has_something_that_does_it(self):
        for step in a.install_plan(a.feature_keys(), NOTHING_THERE, state()):
            if step["kind"] != "note":
                self.assertTrue(callable(a.step_action(step)), step["id"])

    def test_the_entry_opens_the_window_as_the_user(self):
        entry = a.INSTALLER_DESKTOP_ENTRY
        self.assertIn("Exec=/usr/bin/python3 /usr/local/bin/aiov2_ctl --installer", entry)
        self.assertIn("Terminal=false", entry)
        self.assertNotIn("sudo", entry)
        self.assertNotIn("pkexec", entry)

    def test_removing_the_tray_removes_both_entries(self):
        plan = a.uninstall_plan(["tray"], state(present={"desktop-entry"}))
        paths = [p for s in plan for p in s.get("paths", ())]
        self.assertIn(a.SYSTEM_DESKTOP_PATH, paths)
        self.assertIn(a.INSTALLER_DESKTOP_PATH, paths)


class PlainInstallTests(unittest.TestCase):
    """What `--install` with no choice means: it is what every updater ends by running."""

    def test_a_new_machine_gets_the_usual_set(self):
        self.assertEqual(a.plain_selection({}, state()), (a.resolve_features(a.default_features()), []))
        self.assertEqual(a.plain_selection(None, state())[0], a.resolve_features(a.default_features()))

    def test_a_record_is_kept_to_so_an_update_does_not_bring_back_what_was_removed(self):
        chosen, left = a.plain_selection({"features": ["core", "gps"]}, state())
        self.assertEqual(chosen, ["core", "gps"])
        self.assertIn("tray", left)
        self.assertNotIn("gps", left)
        self.assertNotIn("adsb", left)                 # never one of the usual set, so not worth a mention

    def test_a_record_that_misses_what_a_feature_needs_gets_it(self):
        self.assertEqual(a.plain_selection({"features": ["adsb"]}, state())[0], ["core", "sdr", "adsb"])

    def test_an_older_install_with_no_record_gets_the_usual_set_and_keeps_what_it_has(self):
        # What the project this was forked from leaves: the program, completion, the tray entry, the boot unit.
        old = state(present={"exe", "completion", "rails-unit", "desktop-entry", "autostart"}, installed={"readsb"})
        chosen, left = a.plain_selection({"repo_path": "/home/shane/aiov2_ctl", "branch": "main"}, old)
        self.assertEqual(left, [])
        for key in a.default_features() + ["autostart", "adsb"]:
            self.assertIn(key, chosen)

    def test_an_empty_record_counts_as_none(self):
        self.assertEqual(a.plain_selection({"features": []}, state())[0], a.resolve_features(a.default_features()))

    def said(self, args, known):
        lines = []
        code = a.install_command(args, facts=NOTHING_THERE, state=state(), euid=1000, isatty=False,
                                 say=lines.append, known=known)
        return code, "\n".join(lines)

    def test_the_command_says_what_it_is_leaving_alone(self):
        code, text = self.said(["--dry-run"], ["core", "gps"])
        self.assertEqual(code, 0)
        self.assertIn("This will install: core, gps", text)
        self.assertIn("Keeping to what is installed", text)
        self.assertIn("Tray", text)

    def test_a_choice_on_the_command_line_still_wins(self):
        code, text = self.said(["--dry-run", "--only", "tray"], ["core", "gps"])
        self.assertIn("This will install: core, tray", text)
        self.assertNotIn("Keeping to", text)
        code, text = self.said(["--dry-run", "--all"], ["core"])
        self.assertIn(", ".join(a.feature_keys()), text)


class UpdateTests(unittest.TestCase):
    """Updating: the old program copies the new one in, then the new one runs its own installer."""

    def test_a_stamp_is_the_same_for_the_same_file_and_none_for_no_file(self):
        self.assertEqual(a.file_stamp(a.__file__), a.file_stamp(a.__file__))
        self.assertEqual(len(a.file_stamp(a.__file__)), 64)
        self.assertIsNone(a.file_stamp("/nonexistent/aiov2_ctl"))

    def test_an_update_is_unfinished_when_another_versions_installer_ran_last(self):
        self.assertTrue(a.update_unfinished({"installed_by": "old"}, "new"))
        self.assertFalse(a.update_unfinished({"installed_by": "new"}, "new"))

    def test_an_install_from_before_stamps_is_unfinished(self):
        # The project this was forked from, and this one before now: a record with no stamp.
        self.assertTrue(a.update_unfinished({"repo_path": "/home/shane/aiov2_ctl", "branch": "main"}, "new"))
        self.assertTrue(a.update_unfinished({}, "new"))

    def test_nothing_installed_is_not_an_unfinished_update(self):
        self.assertFalse(a.update_unfinished(None, "new"))
        self.assertFalse(a.update_unfinished({"installed_by": "x"}, None))

    def test_the_install_records_the_stamp_of_the_program_that_ran(self):
        written = {}
        ctx = {"source": ("/x/aiov2_ctl.py", None, {"repo_path": "/x"}), "features": ["core"], "stamp": "abc"}
        with mock.patch.object(a, "_write_system_file", side_effect=lambda path, text: written.update({path: text}) or True):
            a._act_core_meta({}, ctx)
        meta = json.loads(written[a.INSTALL_META_PATH])
        self.assertEqual((meta["installed_by"], meta["features"], meta["repo_path"]), ("abc", ["core"], "/x"))

    def test_an_update_runs_the_root_owned_copy_twice_and_never_the_checkout(self):
        first, last = a.update_install_steps("/usr/bin/python3", "/usr/local/bin/aiov2_ctl")
        self.assertEqual(first, ["/usr/bin/python3", "/usr/local/bin/aiov2_ctl", "--install", "--only", "core", "--yes"])
        self.assertEqual(last, ["/usr/bin/python3", "/usr/local/bin/aiov2_ctl", "--install", "--yes", "--show-mesh-status"])
        # The second is a plain install: it keeps to what is installed.
        opts = a.parse_install_args(last[3:])
        self.assertTrue(opts["only"] is None and not opts["all"] and not opts["without"])
        # The first is one every version with features understands, and it copies the program.
        self.assertIn("core.copy", ids(a.install_plan(a.select_features(a.parse_install_args(first[3:])), NOTHING_THERE, state())))

    def test_root_is_asked_for_once(self):
        self.assertEqual(a.update_commands("p", "i", True), [["sudo", "p", "i", "--update-install"]])
        self.assertEqual(a.update_commands("p", "i", True, ["pkexec"]), [["pkexec", "p", "i", "--update-install"]])

    def test_finishing_an_unfinished_update_is_only_the_second_step(self):
        self.assertEqual(a.update_commands("p", "i", False), [["sudo", "p", "i", "--install", "--yes", "--show-mesh-status"]])

    def test_root_comes_from_sudo_on_a_terminal_and_from_the_desktop_otherwise(self):
        def boom():
            raise AssertionError("looked")
        self.assertEqual(a.root_lead(True, boom, "/usr/bin/pkexec"), ["sudo"])
        self.assertEqual(a.root_lead(False, lambda: True, "/usr/bin/pkexec"), ["sudo", "-n"])
        self.assertEqual(a.root_lead(False, lambda: False, "/usr/bin/pkexec"), ["/usr/bin/pkexec"])
        self.assertIsNone(a.root_lead(False, lambda: False, None))

    def test_the_root_half_runs_both_installs_in_order_and_stops_at_a_failure(self):
        ran, said = [], []
        self.assertEqual(a.update_install_command(lambda c: ran.append(c) or 0, said.append, euid=0, trusted=True), 0)
        self.assertEqual([c[2:] for c in ran], [["--install", "--only", "core", "--yes"],
                                               ["--install", "--yes", "--show-mesh-status"]])
        self.assertTrue(all(c[1] == a.INSTALLED_PATH for c in ran))
        self.assertIn("Step 1 of 2", "".join(said))
        ran.clear()
        said.clear()
        self.assertEqual(a.update_install_command(lambda c: ran.append(c) or 1, said.append, euid=0, trusted=True), 1)
        self.assertEqual(len(ran), 1)
        self.assertIn("sudo aiov2_ctl --install", "".join(said))

    def test_the_root_half_refuses_without_root_or_a_root_owned_program(self):
        ran = []
        self.assertEqual(a.update_install_command(ran.append, lambda t: None, euid=1000, trusted=True), 1)
        self.assertEqual(a.update_install_command(ran.append, lambda t: None, euid=0, trusted=False), 1)
        self.assertEqual(ran, [])

    def test_the_update_runs_one_command_and_says_how_to_finish_when_it_fails(self):
        ran, said = [], []
        self.assertEqual(a.finish_update(True, run=lambda c: ran.append(c) or 0, trusted=True, say=said.append,
                                         lead=["sudo"]), 0)
        self.assertEqual([c[-1] for c in ran], ["--update-install"])
        self.assertEqual(a.finish_update(True, run=lambda c: 1, trusted=True, say=said.append, lead=["sudo"]), 1)
        self.assertIn("sudo aiov2_ctl --install", "".join(said))

    def test_no_way_to_root_is_said_and_nothing_runs(self):
        ran, said = [], []
        with mock.patch.object(a, "root_lead", return_value=None):
            self.assertEqual(a.finish_update(True, run=ran.append, trusted=True, say=said.append), 1)
        self.assertEqual(ran, [])
        self.assertIn("aiov2_ctl --update", "".join(said))

    def test_the_tray_offers_to_finish_once_per_version(self):
        self.assertTrue(a.finish_prompt_due(True, "abc", ""))
        self.assertTrue(a.finish_prompt_due(True, "abc", "older\n"))
        self.assertFalse(a.finish_prompt_due(True, "abc", "abc\n"))      # Later was pressed for this version
        self.assertFalse(a.finish_prompt_due(False, "abc", ""))
        self.assertFalse(a.finish_prompt_due(True, None, ""))

    def test_the_notice_is_for_a_person_looking_not_for_a_program_reading(self):
        yes = lambda: True
        self.assertIn("sudo aiov2_ctl --install", a.unfinished_notice_for("--status", [], True, yes))
        self.assertIn("sudo aiov2_ctl --install", a.unfinished_notice_for(None, [], True, yes))
        self.assertEqual(a.unfinished_notice_for("--status", [], False, yes), "")             # piped
        self.assertEqual(a.unfinished_notice_for("--features", ["--json"], True, yes), "")
        self.assertEqual(a.unfinished_notice_for("--gui", [], True, yes), "")
        self.assertEqual(a.unfinished_notice_for("--install", [], True, yes), "")
        self.assertEqual(a.unfinished_notice_for("--status", [], True, lambda: False), "")

    def test_a_quiet_command_does_not_even_look(self):
        def boom():
            raise AssertionError("looked")
        self.assertEqual(a.unfinished_notice_for("--gui", [], True, boom), "")
        self.assertEqual(a.unfinished_notice_for("--status", [], False, boom), "")


class ChannelTests(unittest.TestCase):
    """Channels: stable is main, beta is the beta branch. Switching checks out the other and updates."""

    def test_the_channels(self):
        self.assertEqual((a.channel_of("main"), a.channel_of("beta"), a.channel_of("my-work")), ("stable", "beta", None))
        self.assertEqual((a.channel_branch("stable"), a.channel_branch("beta"), a.channel_branch("x")), ("main", "beta", None))

    def test_the_branch_is_read_from_the_head_file(self):
        self.assertEqual(a.branch_from_head("ref: refs/heads/beta\n"), "beta")
        self.assertEqual(a.branch_from_head("ref: refs/heads/feat/x\n"), "feat/x")
        self.assertIsNone(a.branch_from_head("0123456789abcdef0123456789abcdef01234567\n"))   # not on a branch
        self.assertIsNone(a.branch_from_head(""))

    def test_an_update_follows_the_branch_the_checkout_is_on(self):
        self.assertEqual(a.update_branch({"branch": "main"}, "beta"), "beta")
        self.assertEqual(a.update_branch({"branch": "beta"}, None), "beta")
        self.assertEqual(a.update_branch({"branch": "beta"}, "HEAD"), "beta")
        self.assertEqual(a.update_branch({}, None), "main")
        self.assertEqual(a.update_branch(None, None), "main")

    def test_the_branches_a_repository_has(self):
        text = "aaa\trefs/heads/main\nbbb\trefs/heads/beta\nccc\trefs/tags/v1\n"
        self.assertEqual(a.remote_heads(text), {"main", "beta"})
        self.assertEqual(a.remote_heads(""), set())

    def test_the_report_says_which_channels_there_are(self):
        r = a.channel_report("main", {"main"}, ["abc one", "def two"], "sudo")
        self.assertEqual((r["channel"], r["branch"], r["incoming"], r["root"], r["error"]),
                         ("stable", "main", ["abc one", "def two"], "sudo", ""))
        self.assertEqual(r["channels"], [
            {"name": "stable", "branch": "main", "available": True, "current": True},
            {"name": "beta", "branch": "beta", "available": False, "current": False}])
        r = a.channel_report("my-work", None, None, None, "offline")
        self.assertIsNone(r["channel"])
        self.assertFalse(any(c["available"] or c["current"] for c in r["channels"]))

    def test_the_report_in_words(self):
        text = a.format_channel(a.channel_report("main", {"main", "beta"}, ["abc one"], None))
        self.assertIn("Channel: stable", text)
        self.assertIn("1 change waiting", text)
        self.assertIn("aiov2_ctl --channel beta", text)
        text = a.format_channel(a.channel_report("main", {"main"}, [], None))
        self.assertIn("Up to date.", text)
        self.assertIn("no beta channel", text)

    def test_what_stops_a_switch(self):
        both = {"main", "beta"}
        self.assertEqual(a.switch_problem("beta", "main", both, False), "")
        self.assertEqual(a.switch_problem("stable", "beta", both, False), "")
        self.assertIn("Unknown channel", a.switch_problem("nightly", "main", both, False))
        self.assertIn("Already on the beta channel", a.switch_problem("beta", "beta", both, False))
        self.assertIn("could not be reached", a.switch_problem("beta", "main", None, False))
        self.assertIn("no beta channel yet", a.switch_problem("beta", "main", {"main"}, False))
        self.assertIn("changes of your own", a.switch_problem("beta", "main", both, True))

    def test_what_a_switch_means_is_said_first(self):
        text = a.switch_summary("beta", ["abc one", "def two"], 0)
        self.assertIn("brings 2 changes", text)
        self.assertIn("before they are well tested", text)
        text = a.switch_summary("stable", [], 3)
        self.assertIn("leaves 3 newer changes behind", text)
        self.assertIn("older version", text)
        self.assertNotIn("well tested", text)
        self.assertIn("and 5 more", a.switch_summary("beta", [str(i) for i in range(20)], 0))

    def test_the_apps_folder_follows_the_channel_where_its_repository_has_it(self):
        self.assertEqual(a.app_branch_for("beta", "main", {"main", "beta"}), "beta")
        self.assertEqual(a.app_branch_for("beta", "main", {"main"}), "main")          # no beta there: stay
        self.assertEqual(a.app_branch_for("main", "beta", {"main", "beta"}), "main")  # back to stable
        self.assertEqual(a.app_branch_for("beta", "my-work", {"main", "beta"}), "my-work")   # someone's own branch
        self.assertEqual(a.app_branch_for("my-work", "main", {"main", "beta"}), "main")
        self.assertEqual(a.app_branch_for("beta", "main", None), "main")              # could not look

    def test_looking_at_the_channel_changes_no_file(self):
        calls = []

        def run(cmd, cwd=None, timeout=None):
            calls.append(cmd)
            if "ls-remote" in cmd:
                return 0, "a\trefs/heads/main\n", ""
            if "log" in cmd:
                return 0, "abc one\n", ""
            return 0, "", ""
        self.assertEqual(a.channel_look("/r", "main", run), ({"main"}, ["abc one"], ""))
        self.assertEqual({c[1] for c in calls}, {"ls-remote", "fetch", "log"})
        self.assertEqual(a.channel_look("/r", "main", lambda *x, **k: (1, "", "no network"))[:2], (None, None))

    def test_the_commands_are_wired(self):
        for argv, name in ((["--channel", "beta"], "channel_command"), (["--update-install"], "update_install_command")):
            with mock.patch.object(sys, "argv", ["aiov2_ctl", *argv]), \
                    mock.patch.object(a, name, return_value=0) as cmd, self.assertRaises(SystemExit):
                a.main()
            cmd.assert_called_once()

    def test_a_switch_as_root_or_to_an_unknown_channel_changes_nothing(self):
        with mock.patch.object(a.os, "geteuid", return_value=0), mock.patch.object(a, "run_cmd") as run, \
                mock.patch("builtins.print"):
            self.assertEqual(a.switch_channel("beta", True), 1)
        run.assert_not_called()


class ApplyTests(unittest.TestCase):
    """--apply: make what is installed exactly a list. It is what the window's Install button runs."""

    def test_what_is_installed_and_not_kept_is_removed_last_installed_first(self):
        got = a.apply_changes(["core", "gps"], ["core", "tray", "gps", "flipper", "autostart"])
        self.assertEqual(got["remove"], ["autostart", "flipper", "tray"])
        self.assertEqual(got["install"], ["core", "gps"])

    def test_nothing_is_removed_that_is_not_installed(self):
        self.assertEqual(a.apply_changes(["core", "gps"], [])["remove"], [])
        self.assertEqual(a.apply_changes(["core"], ["core", "sdr"])["remove"], ["sdr"])   # not adsb: it is not there

    def test_what_the_kept_ones_need_is_kept(self):
        got = a.apply_changes(["adsb"], ["core", "sdr", "adsb", "tray"])
        self.assertEqual((got["install"], got["remove"]), (["core", "sdr", "adsb"], ["tray"]))

    def test_the_controller_is_never_removed(self):
        for keep in (["gps"], ["core"], a.feature_keys()):
            self.assertNotIn("core", a.apply_changes(keep, a.feature_keys())["remove"])

    def test_keeping_everything_installed_removes_nothing(self):
        self.assertEqual(a.apply_changes(a.feature_keys(), a.feature_keys())["remove"], [])

    def test_the_arguments(self):
        self.assertEqual(a.parse_apply_args(["--keep", "core,gps", "--dry-run"]),
                         {"keep": ["core", "gps"], "dry_run": True, "yes": False})
        for bad in ([], ["--yes"], ["--keep"], ["--keep", "core", "--all"]):
            with self.assertRaises(a.InstallError):
                a.parse_apply_args(bad)

    def run_apply(self, args, recorded, remove_code=0):
        calls, said = [], []
        code = a.apply_command(args, state=state(), meta={"features": recorded},
                               remove=lambda x: calls.append(("remove", x)) or remove_code,
                               install=lambda x: calls.append(("install", x)) or 0, say=said.append)
        return code, calls, said

    def test_it_removes_then_installs_with_the_ordinary_commands(self):
        code, calls, _ = self.run_apply(["--keep", "core,gps", "--yes"], ["core", "tray", "gps"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("remove", ["--only", "tray", "--yes"]), ("install", ["--only", "core,gps", "--yes"])])

    def test_with_nothing_to_remove_it_only_installs(self):
        _, calls, _ = self.run_apply(["--keep", "core,gps", "--dry-run"], ["core"])
        self.assertEqual(calls, [("install", ["--only", "core,gps", "--dry-run"])])

    def test_a_removal_that_fails_stops_it(self):
        code, calls, _ = self.run_apply(["--keep", "core", "--yes"], ["core", "tray"], remove_code=1)
        self.assertEqual((code, len(calls)), (1, 1))

    def test_bad_arguments_change_nothing(self):
        code, calls, said = self.run_apply(["--keep", "nonsense"], ["core"])
        self.assertEqual((code, calls), (2, []))
        code, calls, said = self.run_apply([], ["core"])
        self.assertEqual((code, calls), (2, []))
        self.assertIn("--keep", "".join(said))

    def test_the_window_says_what_unticking_removes(self):
        st = state(present={"exe", "completion", "rails-unit"})
        text = a.installer_summary(a.FeaturePicker(["core"]), st, ["core", "tray", "autostart"])
        self.assertIn("2 to remove: Tray at login, Tray.", text)
        self.assertNotIn("to remove", a.installer_summary(a.FeaturePicker(["core"]), st, ["core"]))
        self.assertNotIn("to remove", a.installer_summary(a.FeaturePicker(["core"]), st))

    def test_it_is_a_command_with_help_and_completion(self):
        with mock.patch.object(sys, "argv", ["aiov2_ctl", "--apply", "--keep", "core", "--dry-run"]), \
                mock.patch.object(a, "apply_command", return_value=0) as cmd, self.assertRaises(SystemExit):
            a.main()
        cmd.assert_called_once_with(["--keep", "core", "--dry-run"])
        self.assertIn("--apply", a.HELP_TEXT)
        self.assertIn("--apply", a.BASH_COMPLETION)

if __name__ == "__main__":
    unittest.main()
