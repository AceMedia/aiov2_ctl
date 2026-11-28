"""
IGA's tab, as far as it can be checked without Qt: where Qt WebEngine is imported, which engine settings are
asked for, what the error pages say, how permissions are refused, and how the app adds the tab only when the
feature is on. The suite never creates a QApplication and never imports Qt WebEngine.

    python3 -m unittest discover -s tests
"""
import ast
import os
import unittest

from ops_gui import data, iga

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source(*parts):
    with open(os.path.join(HERE, *parts)) as f:
        return f.read()


def webengine_imports(path):
    """(function name, line) of every import of a QtWebEngine module in a file."""
    tree = ast.parse(source(path))
    found = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.Module)):
            for node in ast.walk(fn):
                names = []
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                if any("QtWebEngine" in n for n in names):
                    found.append((getattr(fn, "name", "<module>"), node.lineno))
    return found


class WhereTheEngineIsLoadedTests(unittest.TestCase):
    def test_only_load_engine_imports_qt_webengine(self):
        found = {name for name, _ in webengine_imports(os.path.join("ops_gui", "iga.py")) if name != "<module>"}
        self.assertEqual(found, {"load_engine"})

    def test_no_other_part_of_the_app_imports_it(self):
        folder = os.path.join(HERE, "ops_gui")
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py") and name != "iga.py":
                self.assertEqual(webengine_imports(os.path.join("ops_gui", name)), [], name)
        self.assertNotIn("QtWebEngine", source("aiov2_ctl.py").replace("python3-pyqt6.qtwebengine", ""))

    def test_the_engine_is_loaded_only_from_start_which_runs_on_first_show(self):
        text = source("ops_gui", "iga.py")
        build = text[text.index("def build(win):"):]
        self.assertEqual(build.count("load_engine()"), 1)
        self.assertIn("def showEvent", build)
        self.assertLess(build.index("def showEvent"), build.index("load_engine()"))

    def test_the_sandbox_and_web_security_are_never_weakened(self):
        for path in (("ops_gui", "iga.py"), ("ops_gui", "app.py"), ("aiov2_ctl.py",)):
            text = source(*path)
            for bad in ("--no-sandbox", "QTWEBENGINE_DISABLE_SANDBOX", "--disable-web-security",
                        "WebSecurityEnabled"):
                self.assertNotIn(bad, text, (path, bad))

    def test_the_tab_is_added_only_when_the_feature_is_on(self):
        app = source("ops_gui", "app.py")
        self.assertIn("self.iga_on = iga.wanted()", app)
        self.assertIn("if self.iga_on:\n                self.pages[iga.TAB] = iga.build(self)", app)
        self.assertIn("if iga.wanted():", app)
        self.assertNotIn("IGA", data.tab_names({}, False))
        self.assertEqual(data.tab_names({}, False, iga=True)[-1], "IGA")
        self.assertEqual(iga.TAB, "IGA")

    def test_settings_shows_its_page_only_when_the_feature_is_on(self):
        text = source("ops_gui", "settings_ui.py")
        at = text.index('sub.addTab(scroll, "IGA")')
        self.assertGreater(at, text.index('if getattr(win, "iga_on", False):'))


class KeyTests(unittest.TestCase):
    def test_every_shortcut_is_an_action_and_the_key_press_fallback_agrees(self):
        names = {"Tab": "Tab", "Left": "Left", "Right": "Right"}
        for seq, what in iga.SHORTCUTS:
            parts = seq.split("+")
            key = "+" if seq.endswith("++") else parts[-1]
            ctrl, shift, alt = "Ctrl" in parts, "Shift" in parts, "Alt" in parts
            self.assertEqual(iga.key_action(names.get(key, key), ctrl, shift, alt), what, seq)
        self.assertEqual(iga.key_action("t", ctrl=True), "new")
        self.assertEqual(iga.key_action("Backtab", ctrl=True, shift=True), "previous")

    def test_plain_letters_shift_and_other_keys_are_not_igas(self):
        for args in (("T",), ("T", False, True), ("Left",), ("L", True, False, True), ("Q", True), ("", True)):
            self.assertIsNone(iga.key_action(*args), args)

    def test_no_shortcut_clashes_with_the_main_windows(self):
        app = source("ops_gui", "app.py")
        for seq, _ in iga.SHORTCUTS:
            self.assertTrue(seq.startswith(("Ctrl+", "Alt+")), seq)
            self.assertNotIn(f'QKeySequence("{seq}")', app, seq)

    def test_the_shortcuts_are_parented_to_the_tab_and_scoped_to_it(self):
        text = source("ops_gui", "iga.py")
        self.assertIn("QShortcut(QKeySequence(seq), page)", text)
        self.assertIn("sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)", text)


class FullScreenWiringTests(unittest.TestCase):
    def test_the_apps_full_screen_control_cycles_igas_states_only_on_its_tab(self):
        app = source("ops_gui", "app.py")
        body = app[app.index("        def toggle_fullscreen(self):"):]
        body = body[:body.index("\n        def ", 10)]
        self.assertIn('if self.iga_showing():', body)
        self.assertIn('self.iga_fullscreen("toggle")', body)
        self.assertIn("self.showFullScreen()", body)               # every other tab: as it always was
        self.assertIn("iga.f11_action(", app)
        self.assertIn("self.iga_tab_left()", app)
        self.assertIn("iga.fullscreen_view(state)", app)

    def test_the_new_row_is_built_from_the_pure_layout_and_panels_not_stock_dialogs(self):
        text = source("ops_gui", "iga.py")
        build = text[text.index("def build(win):"):]
        self.assertIn("row_layout(", build)
        self.assertIn("panel_spec(", build)
        for stock in ("QInputDialog", "QFileDialog", "QMessageBox", "QMenu", "QToolBar", "standardIcon"):
            self.assertNotIn(stock, build, stock)
        self.assertNotIn("setStyleSheet(\"color", build)


class EngineSettingsTests(unittest.TestCase):
    def test_what_is_switched_off(self):
        asked = dict(iga.ENGINE_SETTINGS)
        for name in ("DnsPrefetchEnabled", "PluginsEnabled", "LocalContentCanAccessRemoteUrls",
                     "LocalContentCanAccessFileUrls", "ScreenCaptureEnabled", "ErrorPageEnabled"):
            self.assertIs(asked[name], False, name)
        self.assertIs(asked["WebRTCPublicInterfacesOnly"], True)

    def test_the_chromium_flags_add_quiet_networking_and_keep_what_was_there(self):
        self.assertEqual(iga.chromium_flags(None), "--disable-background-networking")
        self.assertEqual(iga.chromium_flags("--foo"), "--foo --disable-background-networking")
        self.assertEqual(iga.chromium_flags("--disable-background-networking"), "--disable-background-networking")

    def test_error_kinds(self):
        self.assertIsNone(iga.error_kind(-3))
        self.assertIsNone(iga.error_kind(0))
        self.assertEqual(iga.error_kind(-105), "dns")
        self.assertEqual(iga.error_kind(-102), "refused")
        self.assertEqual(iga.error_kind(-118), "timeout")
        self.assertEqual(iga.error_kind(-202), "certificate")
        self.assertEqual(iga.error_kind(-999), "other")


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *a):
        for s in self.slots:
            s(*a)


class PermissionTests(unittest.TestCase):
    def test_newer_qt_denies_every_request(self):
        class Permission:
            denied = False

            def deny(self):
                self.denied = True

        class Page:
            permissionRequested = FakeSignal()
        p, perm = Page(), Permission()
        iga.deny_permissions(p, None)
        p.permissionRequested.emit(perm)
        self.assertTrue(perm.denied)

    def test_older_qt_sets_each_feature_denied(self):
        class Policy:
            PermissionDeniedByUser = "denied"

        class QWebEnginePage:
            PermissionPolicy = Policy

        class Page:
            featurePermissionRequested = FakeSignal()
            answers = []

            def setFeaturePermission(self, url, feature, policy):
                self.answers.append((url, feature, policy))
        p = Page()
        iga.deny_permissions(p, QWebEnginePage)
        p.featurePermissionRequested.emit("https://a.org", "Geolocation")
        self.assertEqual(p.answers, [("https://a.org", "Geolocation", "denied")])


if __name__ == "__main__":
    unittest.main()

