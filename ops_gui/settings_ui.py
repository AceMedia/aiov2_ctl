"""The Settings tab: sub tabs that are always shown, whatever rails are powered."""
import json
import os
import subprocess
import sys
import threading

from . import dashboard, data, features, notify, reports

REFRESH_CHOICES = (5, 10, 30, 60)


def build_settings(win, Bridge_signals):
    """Return (widget, hooks). `win` is the main window; it provides ask(), set_theme_choice()
    and the shared settings. Imports are local so the module loads without PyQt6 in tests."""
    from PyQt6.QtCore import Qt, QTimer, QUrl
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                                 QLabel, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget,
                                 QVBoxLayout, QWidget)
    from .foldertabs import FolderTabWidget

    def page():
        inner = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        return scroll, QVBoxLayout(inner)

    def card(title, lay):
        box = QFrame()
        box.setObjectName("card")
        v = QVBoxLayout(box)
        head = QLabel(title.upper())
        head.setObjectName("tag")
        v.addWidget(head, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(box)
        return v

    def label(text="", muted=False):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet("font-size: 13pt;" + (" color: #9e9e9e;" if muted else ""))
        return lab

    def button(text):
        b = QPushButton(text)
        b.setStyleSheet("font-size: 13pt;")
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    sub = FolderTabWidget()
    sub.setObjectName("subtabs")
    sub.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    texts = {}      # key -> label that a background fetch fills in

    # ---- General: two columns of cards; each line is a setting, what it is on the left, its control on the right
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QFontComboBox
    from . import startup, style

    def setting(v, title, hint, control):
        line = QFrame()
        line.setObjectName("setting")
        h = QHBoxLayout(line)
        h.setContentsMargins(2, 7, 2, 7)
        h.setSpacing(18)
        words = QVBoxLayout()
        words.setSpacing(1)
        name = QLabel(title)
        name.setStyleSheet("font-size: 13pt;")
        words.addWidget(name)
        if hint:
            small = QLabel(hint)
            small.setWordWrap(True)
            small.setStyleSheet("font-size: 10pt; color: #9e9e9e;")
            words.addWidget(small)
        h.addLayout(words, 1)
        h.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        v.addWidget(line)
        return line

    def onoff(changed):
        """A switch that says what it is: ON or OFF."""
        b = QPushButton("OFF")
        b.setCheckable(True)
        b.setFixedWidth(92)
        b.setStyleSheet("font-size: 13pt;")
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.toggled.connect(lambda on: b.setText("ON" if on else "OFF"))
        b.clicked.connect(changed)
        return b

    def choices(options, chosen):
        """A row of buttons of which one is down. Returns (the row, {value: its button})."""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        made = {}
        for value, name in options:
            b = button(name)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, v=value: chosen(v))
            row.addWidget(b)
            made[value] = b
        return holder, made

    def columns(lay, wide=1, narrow=1):
        """Two columns of cards side by side on a page. Returns (left, right)."""
        cols = QHBoxLayout()
        cols.setSpacing(10)
        left, right = QVBoxLayout(), QVBoxLayout()
        cols.addLayout(left, wide)
        cols.addLayout(right, narrow)
        lay.addLayout(cols)
        return left, right

    scroll, lay = page()
    left, right = columns(lay)

    v = card("Display", left)
    theme_row, theme_buttons = choices(((None, "System"), ("light", "Light"), ("dark", "Dark")),
                                       lambda c: (win.set_theme_choice(c), refresh_theme()))
    setting(v, "Theme", "Light, dark, or whichever the desktop is using.", theme_row)
    contrast_box = onoff(lambda on: win.set_contrast(on))
    setting(v, "High contrast", "Pure black and white with solid edges, nothing see-through and no gradient.", contrast_box)
    back_row, back_buttons = choices((("gradient", "Gradient"), ("plain", "Plain"), ("black", "Black")),
                                     lambda c: (win.set_background(c), refresh_theme()))
    setting(v, "Background", "The dotted gradient behind everything, one flat colour, or solid black.", back_row)
    font_holder = QWidget()
    font_row = QHBoxLayout(font_holder)
    font_row.setContentsMargins(0, 0, 0, 0)
    font_row.setSpacing(4)
    font_box = QFontComboBox()
    font_box.setMaximumWidth(250)
    font_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    font_own = button("App's own")
    font_own.setCheckable(True)
    font_box.currentFontChanged.connect(lambda f: (win.set_font_family(f.family()), refresh_theme()))
    font_own.clicked.connect(lambda: (win.set_font_family(""), refresh_theme()))
    font_row.addWidget(font_box)
    font_row.addWidget(font_own)
    setting(v, "Font", "Any font on this computer in place of the app's own. Some screens pick it up the next "
                       "time the app is opened.", font_holder)

    def refresh_theme():
        for choice, b in theme_buttons.items():
            b.setChecked(choice == win.saved_theme)
        contrast_box.setChecked(bool(win.flag("high_contrast")))
        for choice, b in back_buttons.items():
            b.setChecked(choice == style.background(win.flag("background")))
        family = win.flag("font") or ""
        font_own.setChecked(not family)
        if family:
            font_box.blockSignals(True)
            font_box.setCurrentFont(QFont(family))
            font_box.blockSignals(False)

    v = card("Feature colour", left)
    v.addWidget(label("Used for borders, the open tab, buttons as you press them and highlights, on every screen. "
                      "Drag in the square and the strip, type a hex colour, or pick a preset. It changes as you go "
                      "and is remembered.", True))
    from .colourpicker import ColourPicker
    picker = ColourPicker(win.feature_colour())
    picker.changed.connect(lambda hx: win.set_feature_colour(hx))
    picker.committed.connect(lambda hx: win.set_feature_colour(hx, save=True))
    v.addWidget(picker)
    left.addStretch(1)

    v = card("Tabs", right)
    home_box = onoff(lambda on: (win.set_flag("home_tab", on), win.rebuild_tabs()))
    setting(v, "Home tab", "A pixel art start screen in Debian colours, with soft keys on F1 to F5.", home_box)

    def apps_switched(on):
        win.set_flag("apps_tab", on)
        if not on and win.flag("games_tab"):                # the tab's first name: off means off under both
            win.set_flag("games_tab", False)
        win.rebuild_tabs()

    apps_box = onoff(apps_switched)
    setting(v, "Apps tab", "The programs installed on this computer as tiles, games first, to start them "
                           "from here.", apps_box)

    v = card("Start-up", right)
    start_note = label("", True)
    start_note.hide()

    def start_switched(on):
        try:
            startup.set_enabled(on)
            start_note.hide()
        except OSError as e:
            start_note.setText(f"Could not change that: {e.strerror or e}")
            start_note.show()
        start_box.setChecked(startup.enabled())

    start_box = onoff(start_switched)
    setting(v, "Open in full screen at login", "The app fills the screen when the desktop starts, like a console's "
            "own menu. No rail is switched on. To leave full screen: F11, Y on the gamepad, or the button at the "
            "top right. To get the taskbar back for a moment, rest the pointer on the bottom edge of the screen.",
            start_box)
    v.addWidget(start_note)
    auto = onoff(lambda on: threading.Thread(
        target=lambda: subprocess.run(["aiov2_ctl", "--autostart" if on else "--no-autostart"],
                                      capture_output=True, timeout=30), daemon=True).start())
    setting(v, "Tray icon at login", "The rails and services menu beside the clock.", auto)

    v = card("Refresh", right)
    combo = QComboBox()
    combo.setStyleSheet("font-size: 13pt;")
    for s_ in REFRESH_CHOICES:
        combo.addItem(f"Every {s_} seconds", s_)
    combo.currentIndexChanged.connect(lambda i: win.set_refresh_seconds(combo.itemData(i)))
    setting(v, "Check the hardware", "How often the Overview looks at the rails and radios.", combo)
    right.addStretch(1)
    lay.addStretch(1)
    sub.addTab(scroll, "General")
    # ---- Notifications
    scroll, lay = page()
    left, right = columns(lay)
    v = card("What to tell me about", left)
    note_boxes = {}
    for key, name in notify.SOURCES:
        box = onoff(lambda on, k=key: win.set_notification(k, on))
        setting(v, name, "", box)
        note_boxes[key] = box
    left.addStretch(1)
    v = card("How", right)
    for key, name, hint in (("toasts", "Pop-up notices inside the app", "A notice over the corner of the window."),
                            ("system", "Desktop notifications too", "The desktop's own, which show when the app "
                                                                    "is behind another window.")):
        box = onoff(lambda on, k=key: win.set_notification(k, on))
        setting(v, name, hint, box)
        note_boxes[key] = box
    daemon_note = label("", True)
    v.addWidget(daemon_note)
    test_b = button("Send")
    test_b.clicked.connect(lambda: win.notify_user("test", "Test notice", "This is what a notice looks like.", "good"))
    setting(v, "Send a test notice", "To see what one looks like.", test_b)
    v.addWidget(label("Mesh notices need the LoRa rail on. The app keeps its connection to the mesh open for them, "
                      "and never sends anything.", True))
    right.addStretch(1)
    lay.addStretch(1)
    sub.addTab(scroll, "Notifications")

    def refresh_notifications():
        for key, box in note_boxes.items():
            box.setChecked(bool(win.notifier.cfg.get(key)))
        daemon_note.setText("A desktop notification service is running." if win.notifier.daemon_available() else
                            "No desktop notification service found, so only in-app notices will show. "
                            "To get desktop ones install one: sudo apt install dunst")

    dash_page = dashboard.build_page(win)
    win.dash_refresh = dash_page.refresh
    sub.addTab(dash_page, "Dashboard")

    # ---- Power
    scroll, lay = page()
    left, right = columns(lay)
    v = card("Rails at boot", left)
    texts["boot"] = label("Checking...")
    v.addWidget(texts["boot"])
    v.addWidget(label("Rails stay off at boot unless you choose otherwise. To change one, run "
                      "sudo aiov2_ctl --boot-rail GPS on (or off).", True))
    left.addStretch(1)
    v = card("Measured rail power", right)
    texts["measured"] = label("")
    v.addWidget(texts["measured"])
    clear_m = button("Clear")

    def do_clear_measurements():
        if win.ask("Forget every saved rail measurement?"):
            try:
                os.remove(data.rail_cache_path())
            except OSError:
                pass
            win.rail_cache = {}
            win.update_rails()
            refresh_measured()

    clear_m.clicked.connect(do_clear_measurements)
    clear_row = setting(v, "Clear measurements", "Forget every saved rail measurement.", clear_m)
    right.addStretch(1)
    lay.addStretch(1)
    sub.addTab(scroll, "Power")

    def refresh_measured():
        cache = data.load_rail_power()
        lines = [f"{data.RAIL_LABEL.get(n, n)}: {data.rail_power_text(n, cache)}" for n in data.RAILS if n in cache]
        texts["measured"].setText("\n".join(lines) or "Nothing measured yet. Measuring is offered on the Overview when the uConsole runs on battery.")
        clear_row.setVisible(bool(lines))

    # ---- LoRa
    scroll, lay = page()
    v = card("Meshtastic", lay)
    texts["mesh"] = label("Checking...")
    v.addWidget(texts["mesh"])
    v.addWidget(label("meshtasticd starts when the LoRa rail comes on. To also start it at boot: "
                      "aiov2_ctl --mesh-on-boot (or --mesh-off-boot).", True))
    lay.addStretch(1)
    sub.addTab(scroll, "LoRa")

    # ---- Wi-Fi
    scroll, lay = page()
    v = card("Saved scans", lay)
    texts["captures"] = label("")
    v.addWidget(texts["captures"])
    v.addWidget(label("Scans are saved by aiov2_ctl --wifi scan --save and kept private to your account.", True))
    lay.addStretch(1)
    sub.addTab(scroll, "Wi-Fi")

    # ---- Flipper
    scroll, lay = page()
    v = card("Flipper", lay)
    texts["flipper"] = label("")
    v.addWidget(texts["flipper"])
    v.addWidget(label("Everything Flipper related is optional and only appears when one is plugged in.", True))
    lay.addStretch(1)
    sub.addTab(scroll, "Flipper")

    # ---- Features: what the installer has put here, and adding or removing one
    scroll, lay = page()
    left, right = columns(lay, 3, 2)
    v = card("Features", left)
    v.addWidget(label("What the installer has set up on this uConsole. Adding or removing one runs the same "
                      "installer as the command line and asks first. Removing never uninstalls a package, "
                      "switches a rail or deletes your saved settings.", True))
    feat_note = label("Checking...")
    v.addWidget(feat_note)
    # Beside the result it belongs to: the Updates card is at the foot of the page, off the screen.
    restart_b = button("Restart the app")
    restart_b.hide()
    v.addWidget(restart_b, 0, Qt.AlignmentFlag.AlignLeft)
    feat_log = QPlainTextEdit()
    feat_log.setReadOnly(True)
    feat_log.setMinimumHeight(160)
    feat_log.setStyleSheet("font-size: 11pt;")
    feat_log.hide()
    v.addWidget(feat_log)
    feat_rows = QVBoxLayout()
    feat_rows.setSpacing(0)
    v.addLayout(feat_rows)
    feat_scroll = scroll
    left.addStretch(1)
    v = card("Installer", right)
    window_b = button("Open")
    window_b.clicked.connect(lambda: subprocess.Popen(["aiov2_ctl", "--installer"], start_new_session=True,
                                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    setting(v, "The installer window", "Does several at once, shows the full plan first, and repairs what is "
                                       "missing.", window_b)
    v = card("Updates", right)
    update_note = label(features.UNFINISHED)
    update_note.hide()
    v.addWidget(update_note)
    channel_l = label(features.CHANNEL_WAIT)
    channel_l.setStyleSheet("font-size: 13pt; font-weight: bold;")
    v.addWidget(channel_l)
    incoming_l = label("", True)
    incoming_l.hide()
    v.addWidget(incoming_l)
    update_row = QHBoxLayout()
    update_b = button("Update now")
    switch_b = button("Switch channel")
    for b in (update_b, switch_b):
        update_row.addWidget(b)
    update_row.addStretch(1)
    v.addLayout(update_row)
    switch_l = label("", True)
    switch_l.hide()
    v.addWidget(switch_l)
    v.addWidget(label(features.UPDATE_HELP, True))
    right.addStretch(1)
    lay.addStretch(1)
    sub.addTab(scroll, "Features")
    feat = {"report": None, "buttons": [], "busy": None, "channel": None, "job": None}

    def show_channel(text=None):
        if text is not None:
            feat["channel"] = features.parse_channel(text)
            feat["channel_seen"] = True
            offer_update()
        ch, report = feat["channel"], feat["report"]
        unfinished = bool(report and report["unfinished"])
        idle = feat["busy"] is None
        if ch is None:
            channel_l.setText(features.CHANNEL_OLD if feat.get("channel_seen") else features.CHANNEL_WAIT)
            incoming_l.hide()
            switch_l.hide()
            switch_b.setText("Switch channel")
            switch_b.setEnabled(False)
            update_b.setEnabled(idle and features.can_update(ch, unfinished, feat.get("channel_seen")))
            return
        channel_l.setText(features.channel_text(ch))
        waiting = features.incoming_text(ch)
        incoming_l.setText(waiting)
        incoming_l.setVisible(bool(waiting))
        update_b.setEnabled(idle and features.can_update(ch, unfinished))
        other = features.other_channel(ch)
        switch_b.setText(f"Switch to {other['name']}" if other else "Switch channel")
        switch_b.setEnabled(idle and bool(other and other["available"]))
        switch_l.setText(features.switch_note(ch))
        switch_l.setVisible(bool(features.switch_note(ch)))

    def offer_update():
        """A pop-up when an update is waiting: Update now, or Dismiss until something newer arrives."""
        n = features.update_notice(feat["channel"], win.settings.get("update_dismissed"), feat["busy"] is not None)
        if not n or feat.get("offered") == n["id"]:
            return

        def update_now():
            # Start first: opening the page looks at the channel again, and that must not run beside the update.
            start_job("update")
            if feat["busy"] is not None:
                win.goto_settings("Features")

        def dismiss():
            win.settings["update_dismissed"] = n["id"]
            data.save_settings(dict(data.load_settings(), update_dismissed=n["id"]))

        if win.notifier.offer("updates", n["title"], n["body"], (("Update now", update_now), ("Dismiss", dismiss))):
            feat["offered"] = n["id"]

    def look_at_channel():
        if feat["busy"] is not None:
            return                                  # an update or a switch is using the checkout

        def work():
            try:
                r = subprocess.run(features.CHANNEL_ARGS, text=True, capture_output=True, timeout=180,
                                   stdin=subprocess.DEVNULL)
                out = r.stdout
            except Exception:
                out = ""
            Bridge_signals.text.emit("channel_json", out)
        threading.Thread(target=work, daemon=True).start()

    def start_job(what):
        report, ch = feat["report"], feat["channel"]
        if feat["busy"] is not None:
            return
        other = features.other_channel(ch)
        name = other["name"] if what == "switch" and other else ""
        if what == "switch" and not name:
            return
        if features.job_root(ch, report) is None:
            # No way to root from here: a terminal can ask for the password, and it asks its own question.
            subprocess.Popen(features.job_argv(what, name, terminal=True), start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        if not win.ask(features.job_confirm_text(what, ch, name)):
            return
        if what == "switch" and name == "beta":
            win.set_flag("crash_reports", True)         # the question just asked says so
        feat["busy"] = (what, name)
        feat["job"] = (what, name)
        for b in (*feat["buttons"], update_b, switch_b):
            b.setEnabled(False)
        restart_b.hide()
        feat_note.setText(features.job_start_text(what, name))
        feat_note.show()
        feat_log.hide()
        feat_scroll.verticalScrollBar().setValue(0)
        argv = features.job_argv(what, name)

        def work():
            try:
                r = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, timeout=3600)
                code, out = r.returncode, r.stdout or ""
            except Exception as e:
                code, out = None, str(e)
            Bridge_signals.text.emit("job_done", json.dumps({"code": code, "out": out}))
        threading.Thread(target=work, daemon=True).start()

    def job_done(payload):
        what, name = feat["job"] or ("update", "")
        feat["busy"] = None
        done = json.loads(payload)
        feat_note.setText(features.job_result_text(what, name, done["code"]))
        feat_note.show()
        feat_log.setPlainText(features.tail(done["out"]))
        feat_log.setVisible(done["code"] != 0 and bool(done["out"].strip()))
        restart_b.setVisible(done["code"] == 0)
        feat_scroll.verticalScrollBar().setValue(0)
        refresh_features(keep_note=True)

    def restart_app():
        root = os.path.dirname(os.path.dirname(os.path.abspath(features.__file__)))
        subprocess.Popen(features.restart_argv(sys.executable), cwd=root, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        win.close()
        QApplication.instance().quit()

    update_b.clicked.connect(lambda: start_job("update"))
    switch_b.clicked.connect(lambda: start_job("switch"))
    restart_b.clicked.connect(restart_app)

    def clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                clear_layout(item.layout())

    def show_features(text):
        report = features.parse_report(text)
        feat["report"] = report
        feat["buttons"] = []
        clear_layout(feat_rows)
        update_note.setVisible(bool(report and report["unfinished"]))
        if report is None:
            feat_note.setText(features.OLD_CLI)
            feat_note.show()
            return
        if feat["busy"] is None:
            feat_note.setVisible(report["root"] is None)
            feat_note.setText("This app cannot get root here, so each button shows the command to type instead.")
        for row in report["features"]:
            frame = QFrame()                                # one line per feature, as on every other page
            frame.setObjectName("setting")
            line = QHBoxLayout(frame)
            line.setContentsMargins(2, 7, 2, 7)
            line.setSpacing(18)
            words = QVBoxLayout()
            words.setSpacing(1)
            name = label(row["title"])
            name.setStyleSheet("font-size: 13pt; font-weight: bold;")
            words.addWidget(name)
            about = label(row["summary"], True)
            about.setStyleSheet("font-size: 10pt; color: #9e9e9e;")
            words.addWidget(about)
            state = label(features.state_text(row))
            state.setStyleSheet("font-size: 11pt;")
            words.addWidget(state)
            reason = features.kept_reason(row)
            if reason:
                why = label(reason, True)
                why.setStyleSheet("font-size: 10pt; color: #9e9e9e;")
                words.addWidget(why)
            line.addLayout(words, 1)
            what = features.action(row)
            if what:
                b = button("Add" if what == "add" else "Remove")
                b.setFixedWidth(110)
                b.setEnabled(feat["busy"] is None)
                b.clicked.connect(lambda _=False, r=row, w=what: change_feature(r, w))
                feat["buttons"].append(b)
                line.addWidget(b, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            feat_rows.addWidget(frame)

    def change_feature(row, what):
        report = feat["report"]
        if feat["busy"] is not None or report is None:
            return
        argv = features.change_argv(report["root"], what, row["key"])
        if argv is None:
            feat_note.setText("Run this in a terminal: " + features.command_text(what, row["key"]))
            feat_note.show()
            return
        if not win.ask(features.confirm_text(row, features.titles(report), what)):
            return
        feat["busy"] = (what, row["title"])
        for b in feat["buttons"]:
            b.setEnabled(False)
        feat_note.setText(("Adding " if what == "add" else "Removing ") + row["title"] + "...")
        feat_note.show()
        feat_log.hide()
        feat_scroll.verticalScrollBar().setValue(0)

        def work():
            try:
                r = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, timeout=1800)
                code, out = r.returncode, r.stdout or ""
            except Exception as e:
                code, out = None, str(e)
            Bridge_signals.text.emit("features_done", json.dumps({"code": code, "out": out}))
        threading.Thread(target=work, daemon=True).start()

    def feature_done(payload):
        what, title = feat["busy"] or ("add", "")
        feat["busy"] = None
        done = json.loads(payload)
        left = what == "add" and done["code"] == 0 and features.left_out(done["out"])
        feat_note.setText(features.result_text(what, title, done["code"], left))
        feat_note.show()
        feat_log.setPlainText(features.tail(done["out"]))
        feat_log.setVisible((done["code"] != 0 or left) and bool(done["out"].strip()))
        feat_scroll.verticalScrollBar().setValue(0)
        refresh_features(keep_note=True)

    def refresh_features(keep_note=False):
        if feat["busy"] is not None:
            return
        if not keep_note:
            feat_log.hide()
        feat["keep_note"] = keep_note
        run_text("features_json", features.REPORT_ARGS)
        look_at_channel()

    def on_features(key, value):
        if key == "features_json":
            keep = feat.pop("keep_note", False)
            note = feat_note.text()
            show_features(value)
            if keep and feat["report"] is not None:
                feat_note.setText(note)
                feat_note.show()
            show_channel()
        elif key == "features_done":
            feature_done(value)
        elif key == "channel_json":
            show_channel(value)
        elif key == "job_done":
            job_done(value)

    Bridge_signals.text.connect(on_features)

    # ---- Debug
    scroll, lay = page()
    left, right = columns(lay)
    v = card("Privacy first", left)
    v.addWidget(label("Nothing is sent anywhere by this app. Logging and crash reports are off until "
                      "you turn them on, they stay on this uConsole, and sharing is always a step you "
                      "take yourself.", True))
    log_box = onoff(lambda on: win.set_flag("debug_log", on))
    crash_box = onoff(lambda on: win.set_flag("crash_reports", on))
    setting(v, "Keep a local log", "What the app does, kept on this uConsole.", log_box)
    setting(v, "Save crash reports", "A local report if the app crashes.", crash_box)
    v.addWidget(label("With the log on, the app also keeps a field log for reading afterwards: every few seconds "
                      "the GPS reading (position, altitude, satellites), and every half minute the rails, battery, "
                      "temperature and what each scan has gathered. It holds positions, so it is never put into a "
                      "report. It is in ~/.local/state/ops_gui and Clear removes it.", True))
    texts["fieldlog"] = label("", True)        # whether lines are really reaching the file, and when one last did
    v.addWidget(texts["fieldlog"])
    texts["crashes"] = label("", True)
    v.addWidget(texts["crashes"])

    v = card("Report a problem", right)
    v.addWidget(label("Prepare a report to read it first. It has versions, the status and --doctor output "
                      "and the recent log, with your username, host name, IP and MAC addresses removed. "
                      "No Wi-Fi scans or captures are included.", True))
    prepare = button("Prepare report")
    v.addWidget(prepare)
    preview = QPlainTextEdit()
    preview.setReadOnly(True)
    preview.setMinimumHeight(180)
    preview.setPlaceholderText("The report appears here.")
    preview.setStyleSheet("font-size: 12pt;")
    v.addWidget(preview)
    acts = QHBoxLayout()
    copy_b, save_b, issue_b = button("Copy"), button("Save to file"), button("Open a GitHub issue")
    find_b = button("Find this crash on GitHub")
    for b in (copy_b, save_b, find_b, issue_b):
        b.setEnabled(False)
        acts.addWidget(b)
    acts.addStretch(1)
    v.addLayout(acts)
    v.addWidget(label("Open a GitHub issue copies the report and opens a blank issue in your browser. "
                      "Paste it in and submit it if you are happy with it. After a crash, the issue's title "
                      "carries a short code for that crash. Find this crash on GitHub searches for the code, "
                      "so you can add to an issue that is already there instead of opening another.", True))

    def do_prepare():
        prepare.setEnabled(False)
        prepare.setText("Preparing...")
        names = list(win.names)
        threading.Thread(target=lambda: Bridge_signals.report.emit(reports.build_report(names)),
                         daemon=True).start()

    def got_report(text):
        preview.setPlainText(text)
        prepare.setEnabled(True)
        prepare.setText("Prepare report")
        for b in (copy_b, save_b, issue_b):
            b.setEnabled(bool(text))
        crash = reports.latest_crash()
        find_b.setEnabled(bool(text and crash and reports.find_url(crash[1])))

    def do_copy():
        QApplication.clipboard().setText(preview.toPlainText())

    def do_save():
        path, _ = QFileDialog.getSaveFileName(win, "Save report", os.path.expanduser("~/uconsole-report.txt"))
        if path:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(preview.toPlainText())

    def do_issue():
        do_copy()
        crash = reports.latest_crash()
        url = reports.issue_url(reports.crash_title(crash[1]), ("crash",)) if crash else reports.issue_url()
        QDesktopServices.openUrl(QUrl(url))

    def do_find():
        crash = reports.latest_crash()
        url = reports.find_url(crash[1]) if crash else None
        if url:
            do_copy()
            QDesktopServices.openUrl(QUrl(url))

    def offer_crash():
        """A pop-up when a crash report was saved and nobody has looked at it: Report, or Dismiss."""
        n = reports.crash_notice(reports.latest_crash(), win.settings.get("crash_dismissed"))
        if not n:
            return

        def seen():
            win.settings["crash_dismissed"] = n["id"]
            data.save_settings(dict(data.load_settings(), crash_dismissed=n["id"]))

        def report():
            seen()
            win.goto_settings("Debug")
            do_prepare()

        win.notifier.offer("crashes", n["title"], n["body"], (("Report", report), ("Dismiss", seen)), "warn")

    prepare.clicked.connect(do_prepare)
    copy_b.clicked.connect(do_copy)
    save_b.clicked.connect(do_save)
    issue_b.clicked.connect(do_issue)
    find_b.clicked.connect(do_find)
    QTimer.singleShot(9000, offer_crash)
    Bridge_signals.report.connect(got_report)

    v = card("Clear", left)
    clear_b = button("Delete")

    def do_clear():
        if win.ask("Delete the local log and every saved crash report?"):
            n = reports.clear_all()
            texts["crashes"].setText(f"Deleted {n} file(s).")
            preview.clear()

    clear_b.clicked.connect(do_clear)
    setting(v, "Delete the local log and crash reports", "The log, the field log and every saved crash report.",
            clear_b)
    left.addStretch(1)
    right.addStretch(1)
    lay.addStretch(1)
    sub.addTab(scroll, "Debug")

    # ---- refreshing what is shown when the tab opens
    def run_text(key, args, fmt=lambda s: s):
        def work():
            try:
                r = subprocess.run(args, text=True, capture_output=True, timeout=20)
                out = (r.stdout or r.stderr).strip()
            except Exception as e:
                out = f"(could not run: {e})"
            Bridge_signals.text.emit(key, fmt(out))
        threading.Thread(target=work, daemon=True).start()

    def on_text(key, value):
        if key in texts:
            texts[key].setText(value)

    Bridge_signals.text.connect(on_text)

    def refresh():
        refresh_theme()
        dash_page.refresh()
        refresh_notifications()
        home_box.setChecked(win.flag("home_tab") is not False)
        apps_box.setChecked(bool(win.flag("apps_tab") or win.flag("games_tab")))
        idx = REFRESH_CHOICES.index(win.refresh_seconds) if win.refresh_seconds in REFRESH_CHOICES else 0
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        auto.setChecked(os.path.exists(os.path.expanduser("~/.config/autostart/aiov2_ctl.desktop")))
        start_box.setChecked(startup.enabled())
        log_box.setChecked(bool(win.flag("debug_log")))
        crash_box.setChecked(bool(win.flag("crash_reports")))
        from . import fieldlog
        note = fieldlog.status_text(getattr(win, "fieldlog", None), bool(win.flag("debug_log")))
        texts["fieldlog"].setText(note)
        texts["fieldlog"].setVisible(bool(note))
        n = len(reports.pending_crashes())
        texts["crashes"].setText(f"{n} saved crash report(s) on this device." if n else "No crash reports saved.")
        refresh_measured()
        run_text("boot", ["aiov2_ctl", "--boot-rails-status"])
        run_text("mesh", ["aiov2_ctl", "--mesh-on-boot", "status"])
        refresh_features()
        folder = os.path.expanduser("~/aiov2-captures")
        try:
            n = len([f for f in os.listdir(folder) if f.startswith("wifi-scan-")])
            texts["captures"].setText(f"{folder}: {n} saved scan(s).")
        except OSError:
            texts["captures"].setText("No saved scans yet.")
        st = getattr(win, "flip_status", {}) or {}
        mods = st.get("declared_modules") or []
        texts["flipper"].setText("Declared add-on boards: " + (", ".join(mods) if mods else "none"))

    # Look for an update shortly after the app starts, then a few times a day, so the notice can offer it.
    QTimer.singleShot(20000, look_at_channel)
    update_timer = QTimer(sub)
    update_timer.timeout.connect(look_at_channel)
    update_timer.start(6 * 3600 * 1000)
    return sub, refresh
