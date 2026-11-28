"""The Everything page's own logic, run with a stand-in for Qt.

There is no Qt where the tests run, so this is not a test of the real widgets or of how anything looks. The
stand-in has only the handful of Qt calls the page makes (anything else is an error, so a misspelt name fails),
signals deliver at once, and threads run on the spot. What is tested is what the page decides: which scans it
starts and stops, what it writes, and that it never asks for a rail to be switched.
"""
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

from ops_gui import bt_panel, drawer, everything as ev, home_panel, reports, sense, statusbar, survey, walkmap

T0 = 1795000000.0
TPV = '{"class":"TPV","mode":3,"lat":53.1,"lon":-1.2,"altMSL":203.9,"eph":4.2}'
SKY = '{"class":"SKY","hdop":0.9,"nSat":3,"uSat":2,"satellites":[{"PRN":1,"ss":41.0,"used":true}]}'


class Signal:
    def __init__(self, *_types):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class Widget:
    def __init__(self, *_a):
        self.enabled, self.visible = True, True

    def setMinimumHeight(self, _h): pass
    def setFocusPolicy(self, _p): pass
    def setStyleSheet(self, _s): pass
    def setToolTip(self, _t): pass
    def setFocus(self): pass
    def update(self): pass
    def rect(self): return (0, 0, 900, 300)
    def width(self): return 900
    def height(self): return 300
    def showEvent(self, _e): pass
    def hideEvent(self, _e): pass
    def keyPressEvent(self, _e): pass
    def setEnabled(self, on): self.enabled = bool(on)
    def isEnabled(self): return self.enabled
    def setVisible(self, on): self.visible = bool(on)
    def isVisible(self): return self.visible


class Label(Widget):
    def __init__(self, text=""):
        super().__init__()
        self.words = text

    def setText(self, text): self.words = text
    def text(self): return self.words
    def setWordWrap(self, _on): pass
    def setTextFormat(self, _f): pass


class Button(Label):
    def __init__(self, text=""):
        super().__init__(text)
        self.clicked = Signal()


class Combo(Widget):
    def __init__(self):
        super().__init__()
        self.items, self.at = [], -1
        self.activated, self.currentIndexChanged = Signal(), Signal()

    def addItem(self, label, value=None):
        self.items.append((label, value))
        self.at = max(self.at, 0)

    def clear(self): self.items, self.at = [], -1
    def count(self): return len(self.items)
    def itemData(self, i): return self.items[i][1]
    def itemText(self, i): return self.items[i][0]
    def currentData(self): return self.items[self.at][1] if 0 <= self.at < len(self.items) else None
    def currentIndex(self): return self.at
    def setCurrentIndex(self, i): self.at = i
    def findData(self, value): return next((i for i, (_l, v) in enumerate(self.items) if v == value), -1)
    def blockSignals(self, _on): pass


class TextBox(Widget):
    def __init__(self):
        super().__init__()
        self.words = ""

    def setReadOnly(self, _on): pass
    def setPlainText(self, text): self.words = text
    def toPlainText(self): return self.words


class InputDialog:
    """The note asked for after Bookmark: what is typed, and whether Enter (True) or Escape (False) was pressed."""
    answer = ("", True)
    asked = 0

    @classmethod
    def getText(cls, *_a):
        cls.asked += 1
        return cls.answer


class Timer:
    def __init__(self, _parent=None):
        self.timeout, self.running = Signal(), False

    def start(self, _ms=0): self.running = True
    def stop(self): self.running = False

    @staticmethod
    def singleShot(_ms, _slot): pass


class NowThread:
    """A thread that does its work on the spot, so a test sees the result straight away."""

    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


def stand_in_for_qt():
    core, gui, widgets = (types.ModuleType(n) for n in ("PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets"))
    core.QObject, core.QTimer, core.pyqtSignal, core.Qt, core.QPoint = object, Timer, Signal, mock.MagicMock(), mock.MagicMock()
    gui.QColor, gui.QFont, gui.QPainter = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
    gui.QPen, gui.QShortcut, gui.QKeySequence = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
    gui.QFontDatabase = types.SimpleNamespace(families=lambda: [])
    widgets.QWidget, widgets.QLabel, widgets.QPushButton, widgets.QComboBox = Widget, Label, Button, Combo
    widgets.QPlainTextEdit, widgets.QInputDialog = TextBox, InputDialog
    widgets.QFrame, widgets.QHBoxLayout, widgets.QVBoxLayout = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
    package = types.ModuleType("PyQt6")
    package.QtCore, package.QtGui, package.QtWidgets = core, gui, widgets
    return {"PyQt6": package, "PyQt6.QtCore": core, "PyQt6.QtGui": gui, "PyQt6.QtWidgets": widgets}


class Cell:
    def __init__(self, text):
        self.words = text

    def text(self):
        return self.words


class Table:
    def __init__(self):
        self.rows = []

    def rowCount(self): return len(self.rows)
    def item(self, r, c): return Cell(self.rows[r][c])


class ScanPage:
    """A scan page as the session sees it: a feed while it runs, a table of what it has heard."""

    def __init__(self, heard):
        self.feed, self.waiting, self.heard, self.starts, self.stops = None, 0.0, heard, 0, 0
        self.table, self.stas, self.ap_list = Table(), Table(), []

    def start(self):
        self.starts += 1
        self.feed = object()
        self.fill()

    def stop(self):
        self.stops += 1
        self.feed = None

    def fill(self):
        self.table.rows = [("name", "maker", addr) for addr in self.heard]
        self.stas.rows = [(addr,) for addr in self.heard]
        self.ap_list = [{"bssid": addr} for addr in self.heard]


class Window:
    """Only what the page may touch. There is no way to switch a rail here at all: reaching for one is an error."""

    def __init__(self, rails):
        self.theme, self.snap = "dark", {"rails": dict(rails), "power": {"capacity": 80, "power": -3.0}}
        self.bt, self.survey = ScanPage(["aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"]), ScanPage(["bb:bb:bb:bb:bb:01"])
        self.pages = {"Bluetooth": self.bt}
        self.wifi_panel = types.SimpleNamespace(survey=self.survey, sense=types.SimpleNamespace(feed=None))
        self.mesh_hub = types.SimpleNamespace(engine=None)
        self.activity = drawer.ActivityLog()
        self.logged = []
        self.fieldlog = types.SimpleNamespace(event=lambda kind, **fields: self.logged.append((kind, fields)))


class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = T0
        self.usb, self.gpsd_asked = True, 0
        self.captures = os.path.join(self.tmp.name, "captures")           # never the real folder of saved logs
        self.aircraft_file = os.path.join(self.tmp.name, "aircraft.json")  # nor a real receiver's file

        def gpsd(*_a):
            self.gpsd_asked += 1
            return [TPV, SKY]
        for patch in (mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name}),
                      mock.patch.dict(sys.modules, stand_in_for_qt()),
                      mock.patch.object(ev.threading, "Thread", NowThread),
                      mock.patch.object(ev.time, "time", lambda: self.now),
                      mock.patch.object(bt_panel, "adapter_available", lambda *_a: self.usb),
                      mock.patch.object(sense, "air_available", lambda *_a: self.usb),
                      mock.patch.object(sense, "read_levels", lambda *_a: {}),
                      mock.patch.object(home_panel, "scan_processes", lambda: (None, False)),
                      mock.patch.object(home_panel, "query_gpsd", gpsd),
                      mock.patch.object(walkmap, "log_dir", lambda: self.captures),
                      mock.patch.object(ev, "AIRCRAFT_FILE", self.aircraft_file),
                      mock.patch.object(statusbar, "read_stats", lambda *_a: {"temp": 50.0})):
            patch.start()
            self.addCleanup(patch.stop)

    def page(self, **rails):
        self.win = Window(dict({"GPS": "off", "LORA": "off", "SDR": "off", "USB": "on"}, **rails))
        self.p = ev.build_page(self.win)
        return self.p

    def seconds(self, count):
        """Let that many seconds go by, with the page's own timer ticking once a second while it records."""
        for _ in range(count):
            self.now += 1.0
            if self.p.timer.running:
                self.p.timer.timeout.emit()

    def lines(self):
        with open(ev.files()[-1]) as f:
            return [json.loads(line) for line in f]

    def test_start_begins_the_scans_whose_radios_are_there_and_stop_ends_them(self):
        p = self.page()
        p.go.clicked.emit()
        self.assertTrue(p.recording)
        self.assertEqual((self.win.bt.starts, self.win.survey.starts), (1, 1))
        self.assertEqual(p.owned, {"wifi", "bt"})
        self.assertEqual(p.go.text(), "Stop")
        self.seconds(12)
        p.chart.paintEvent(None)                                   # few lanes: the roomy two line labels
        p.go.clicked.emit()
        self.assertFalse(p.recording)
        self.assertEqual((self.win.bt.stops, self.win.survey.stops), (1, 1))
        kinds = [r["k"] for r in self.lines()]
        self.assertEqual((kinds[0], kinds[-1], kinds.count("sample")), ("start", "stop", 3))
        first = next(r for r in self.lines() if r["k"] == "sample")
        self.assertEqual(first["wifi"], {"nets": 1, "devs": 1, "nets_total": 1, "devs_total": 1})
        self.assertEqual(first["bt"], {"addrs": 2, "addrs_total": 2})
        self.assertEqual(first["sys"], {"battery": 80, "watts": -3.0, "temp": 50.0})
        self.assertNotIn("gps", first)
        self.assertEqual(self.gpsd_asked, 0)

    def test_a_scan_that_was_already_running_is_used_and_left_running_after_stop(self):
        p = self.page()
        self.win.bt.start()                                        # started by hand on its own page
        p.start()
        self.assertEqual(self.win.bt.starts, 1)
        self.assertEqual(p.owned, {"wifi"})
        self.seconds(6)
        p.stop()
        self.assertEqual((self.win.bt.stops, self.win.survey.stops), (0, 1))
        self.assertIsNotNone(self.win.bt.feed)
        self.assertIn("already running", self.lines()[0]["sources"]["bt"]["text"])

    def test_with_the_usb_rail_off_nothing_is_started_and_the_session_still_records_the_uconsole(self):
        self.usb = False
        p = self.page(USB="off")
        p.start()
        self.seconds(6)
        p.stop()
        self.assertEqual((self.win.bt.starts, self.win.survey.starts, self.win.bt.stops), (0, 0, 0))
        samples = [r for r in self.lines() if r["k"] == "sample"]
        self.assertEqual([sorted(set(r) - {"k", "t", "ts"}) for r in samples], [["sys"], ["sys"]])
        self.assertIn("USB rail is off", self.lines()[0]["sources"]["wifi"]["text"])

    def test_a_rail_that_comes_on_during_the_session_is_picked_up_and_one_that_goes_off_is_dropped(self):
        p = self.page()
        p.start()
        self.seconds(6)
        self.win.snap = dict(self.win.snap, rails=dict(self.win.snap["rails"], GPS="on"))   # the user switched it
        self.seconds(6)
        self.win.snap = dict(self.win.snap, rails=dict(self.win.snap["rails"], GPS="off"))
        self.seconds(6)
        p.stop()
        recs = self.lines()
        texts = [r["text"] for r in recs if r["k"] == "event"]
        for wanted in ("GPS rail on", "GPS joined the session", "GPS rail off", "GPS dropped out: the GPS rail is off"):
            self.assertIn(wanted, texts)
        self.assertTrue(any(t.startswith("First GPS fix") for t in texts))
        with_gps = [r for r in recs if r["k"] == "sample" and "gps" in r]
        self.assertEqual(len(with_gps), 1)
        self.assertEqual((with_gps[0]["lat"], with_gps[0]["lon"]), (53.1, -1.2))
        self.assertNotIn("gps", [r for r in recs if r["k"] == "sample"][-1])
        self.assertEqual(self.gpsd_asked, 1)                       # gpsd is only asked while the rail is on

    def test_the_bluetooth_radio_appearing_during_the_session_starts_its_scan_and_it_is_ours_to_stop(self):
        self.usb = False
        p = self.page(USB="off")
        p.start()
        self.seconds(6)
        self.usb = True
        self.seconds(6)
        self.assertEqual((self.win.bt.starts, self.win.survey.starts), (1, 1))
        p.stop()
        self.assertEqual((self.win.bt.stops, self.win.survey.stops), (1, 1))

    def test_a_scan_of_ours_stopped_by_hand_is_not_started_again(self):
        p = self.page()
        p.start()
        self.seconds(6)
        self.win.bt.feed = None                                    # Stop pressed on the Bluetooth page
        self.seconds(12)
        self.assertEqual(self.win.bt.starts, 1)
        self.assertEqual(p.owned, {"wifi"})
        p.stop()
        self.assertEqual(self.win.bt.stops, 0)
        self.assertIn("Bluetooth scan was stopped outside this session, so it is left off",
                      [r["text"] for r in self.lines() if r["k"] == "event"])

    def test_a_scan_of_ours_that_ran_its_ten_minutes_is_started_again(self):
        p = self.page()
        p.start()
        self.seconds(bt_panel.SECONDS - 5)
        self.win.bt.feed = None                                    # it ran out by itself
        self.seconds(10)
        self.assertEqual(self.win.bt.starts, 2)
        self.assertIn("bt", p.owned)
        p.stop()

    def test_network_and_security_notices_from_the_activity_log_become_events_only_while_recording(self):
        p = self.page()
        self.win.activity.add("New network: Before (aa:bb:cc:dd:ee:ff), WPA2.")
        p.start()
        self.win.activity.add("New network: Cafe (aa:bb:cc:dd:ee:ff), WPA2.")
        self.win.activity.add("shell: ls")
        p.stop()
        self.win.activity.add("New network: After (aa:bb:cc:dd:ee:ff), WPA2.")
        texts = [r["text"] for r in self.lines() if r["k"] == "event"]
        self.assertEqual([t for t in texts if t.startswith("New network")], ["New network: Cafe (aa:bb:cc:dd:ee:ff), WPA2."])
        self.assertEqual(self.win.activity.listeners, [])

    def test_the_start_and_stop_are_offered_to_the_field_log_with_the_summary(self):
        p = self.page()
        p.start()
        self.seconds(11)
        p.stop()
        (kind_a, start), (kind_b, stop) = self.win.logged
        self.assertEqual((kind_a, kind_b), ("session", "session"))
        self.assertEqual((start["what"], start["sources"]), ("everything_start", ["bt", "sys", "wifi"]))
        self.assertEqual((stop["what"], stop["samples"], stop["seconds"]), ("everything_stop", 3, 11.0))
        self.assertEqual(stop["file"], os.path.basename(ev.files()[-1]))

    def test_after_stop_the_session_is_shown_for_review_and_can_be_exported_and_deleted(self):
        p = self.page()
        p.start()
        self.seconds(11)
        p.stop()
        self.assertIn("Stopped after 11 s: 3 readings.", p.state.text())
        self.assertIn("What changed together", p.notes.toPlainText())
        self.assertIn("Bluetooth addresses: 2 to 2, last 2", p.notes.toPlainText())
        self.assertTrue(p.export.isEnabled() and p.remove.isEnabled())
        self.assertEqual(p.pick.currentData(), p.session.path)
        p.export.clicked.emit()
        self.assertEqual(len(ev.all_files()), 2)
        p.remove.clicked.emit()                                    # the first press only asks
        self.assertEqual(p.remove.text(), "Really delete?")
        self.assertEqual(len(ev.all_files()), 2)
        p.remove.clicked.emit()
        self.assertEqual(ev.all_files(), [])
        self.assertIsNone(p.session)
        self.assertFalse(p.export.isEnabled())

    def test_a_saved_session_can_be_opened_from_the_picker(self):
        p = self.page()
        p.start()
        self.seconds(11)
        p.stop()
        path = p.session.path
        q = ev.build_page(self.win)
        q.showEvent(None)
        self.assertEqual((q.pick.count(), q.pick.itemText(0)), (2, "Saved sessions (1)"))
        q.pick.activated.emit(1)
        self.assertEqual((q.session.path, len(q.session.samples)), (path, 3))
        self.assertIn("Saved session of", q.state.text())
        self.assertIn("ran 11 s, 3 readings", q.notes.toPlainText())

    def test_bookmark_keeps_the_last_minutes_of_a_recording_with_a_note_and_opens_like_a_session(self):
        p = self.page()
        InputDialog.answer, InputDialog.asked = ("", True), 0
        p.mark.clicked.emit()                                      # nothing held yet: said, and nothing asked
        self.assertIn("Nothing to bookmark", p.detail.text())
        self.assertEqual((InputDialog.asked, ev.all_files()), (0, []))
        p.start()
        self.seconds(400)
        asked_gps = self.gpsd_asked
        InputDialog.answer = ("by the gate", True)
        p.mark.clicked.emit()
        self.assertEqual(self.gpsd_asked, asked_gps)               # no reading of its own
        self.assertTrue(p.recording)
        marks = [f for f in ev.files() if "-bookmark" in f]
        self.assertEqual(len(marks), 1)
        self.assertIn("Bookmarked 61 readings (5 min 00 s)", p.detail.text())
        self.assertEqual(stat.S_IMODE(os.stat(marks[0]).st_mode), 0o600)
        InputDialog.answer = ("", False)                            # Escape: nothing saved
        p.mark.clicked.emit()
        self.assertIn("Bookmark not saved", p.detail.text())
        p.stop()
        q = ev.build_page(self.win)
        q.showEvent(None)
        q.pick.activated.emit(q.pick.findData(marks[0]))
        self.assertEqual(len(q.session.samples), 61)
        self.assertIn("Bookmark ", q.state.text())
        self.assertIn("Note: by the gate", q.state.text())

    def test_the_chart_draws_and_scrubs_without_falling_over(self):
        p = self.page(GPS="on")
        p.chart.paintEvent(None)                                   # nothing recorded yet
        p.start()
        self.win.activity.add("Deauthentication burst: 40 frames")
        self.seconds(30)
        p.chart.paintEvent(None)
        press = types.SimpleNamespace(position=lambda: types.SimpleNamespace(x=lambda: 300.0))
        p.chart.mousePressEvent(press)
        self.assertIsNotNone(p.chart.scrub)
        self.assertEqual(p.reading.text(), ev.readout(ev.sample_at(p.session, p.chart.scrub)))
        p.chart.paintEvent(None)
        p.chart.mouseDoubleClickEvent(None)
        self.assertIsNone(p.chart.scrub)
        self.assertEqual(p.reading.text(), ev.readout(p.session.samples[-1]))
        p.span_pick.setCurrentIndex(1)
        p.span_pick.currentIndexChanged.emit(1)
        p.stop()
        p.chart.paintEvent(None)

    def test_closing_the_app_ends_a_running_session_with_its_last_line(self):
        p = self.page()
        p.start()
        self.seconds(6)
        p.shutdown()
        self.assertEqual(self.lines()[-1]["k"], "stop")
        self.assertEqual(self.win.bt.stops, 1)
        p.shutdown()                                               # and again with nothing running: nothing happens
        self.assertEqual(self.win.bt.stops, 1)

    def test_a_finished_session_is_cleared_with_the_logs(self):
        p = self.page()
        p.start()
        self.seconds(6)
        p.stop()
        self.assertEqual(reports.clear_all(), 1)
        self.assertEqual(ev.files(), [])

    def walk(self, p, seconds=12):
        """A session with the GPS on, during which the survey page saved a wardrive log."""
        p.start()
        self.seconds(seconds)
        log = survey.WigleLog()
        log.add({"aps": [{"bssid": "AA:BB:CC:DD:EE:01", "ssid": "Cafe", "rssi": -61, "security": "WPA2", "channel": 6}]},
                {"mode": 3, "lat": 53.1, "lon": -1.2}, now=T0 + 4)
        log.add({"aps": [{"bssid": "AA:BB:CC:DD:EE:02", "ssid": "Earlier", "rssi": -50, "security": "WPA2", "channel": 1}]},
                {"mode": 3, "lat": 53.1, "lon": -1.2}, now=T0 - 500)
        log.save(self.captures, now=self.now)
        p.stop()

    def tap(self, x, y):
        return types.SimpleNamespace(position=lambda: types.SimpleNamespace(x=lambda: x, y=lambda: y))

    def test_the_map_shows_the_track_and_what_the_saved_logs_heard_during_the_session(self):
        p = self.page(GPS="on")
        self.assertFalse(p.map_b.isEnabled())
        self.walk(p)
        self.assertEqual(len(p.track), 3)
        self.assertEqual([(r["name"], r["addr"]) for r in p.heard], [("Cafe", "aa:bb:cc:dd:ee:01")])   # not "Earlier"
        self.assertFalse(p.map.isVisible())
        self.assertTrue(p.notes.isVisible())
        p.map_b.clicked.emit()
        self.assertTrue(p.map.isVisible() and p.map_info.isVisible())
        self.assertFalse(p.notes.isVisible())
        self.assertEqual(p.map_b.text(), "Summary")
        self.assertIn("where the uConsole was when it heard this strongest, not where the device is", p.map_info.text())
        self.assertIn("3 positions of the uConsole. 1 Wi-Fi network and 0 Bluetooth addresses", p.map_info.text())
        p.map.paintEvent(None)
        p.map.mousePressEvent(self.tap(450.0, 150.0))                # everything was heard from one spot: the middle
        self.assertIn("The uConsole was here (53.10000, -1.20000) when it heard this strongest", p.map_info.text())
        self.assertIn("Cafe  aa:bb:cc:dd:ee:01  -61 dBm", p.map_info.text())
        p.map.paintEvent(None)
        p.map_b.clicked.emit()
        self.assertTrue(p.notes.isVisible())
        self.assertEqual(p.map_b.text(), "Map")

    def test_touching_the_track_moves_the_chart_and_scrubbing_the_chart_moves_you_were_here(self):
        p = self.page(GPS="on")
        p.start()
        self.seconds(12)
        p.stop()
        self.assertEqual(p.heard, [])
        p.map_b.clicked.emit()
        self.assertIsNone(p.map.here)
        p.map.mouseMoveEvent(self.tap(450.0, 150.0))
        self.assertIn(p.chart.scrub, [s["ts"] for s in p.session.samples])
        self.assertIsNotNone(p.map.here)
        self.assertEqual(p.reading.text(), ev.readout(ev.sample_at(p.session, p.chart.scrub)))
        p.map.paintEvent(None)
        p.map.mousePressEvent(self.tap(5.0, 5.0))                    # nowhere near the track: nothing changes
        self.assertIsNotNone(p.map.here)
        p.chart.mouseDoubleClickEvent(None)                          # let go of the chart: no moment is picked
        self.assertIsNone(p.map.here)
        p.chart.scrub = p.session.samples[0]["ts"]
        p.chart.scrubbed.emit()
        self.assertEqual(p.map.here, 0)

    def test_the_walk_can_be_exported_as_gpx_and_geojson_and_delete_takes_them_too(self):
        p = self.page(GPS="on")
        self.walk(p)
        p.gpx.clicked.emit()
        p.geojson.clicked.emit()
        names = sorted(os.path.basename(f).split(".", 1)[1] for f in ev.all_files())
        self.assertEqual(names, ["geojson", "gpx", "jsonl"])
        for path in ev.all_files():
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        doc = json.load(open(walkmap.export_path(p.session.path, "geojson")))
        self.assertEqual([f["geometry"]["type"] for f in doc["features"]], ["LineString", "Point"])
        self.assertIn("(private)", p.detail.text())
        self.assertIn("not where the device is", p.detail.text())
        p.remove.clicked.emit()
        p.remove.clicked.emit()
        self.assertEqual(ev.all_files(), [])
        self.assertEqual((p.track, p.heard), ([], None))
        self.assertFalse(p.map.isVisible() or p.gpx.isEnabled() or p.geojson.isEnabled() or p.map_b.isEnabled())

    def test_with_no_fix_the_map_says_so_and_nothing_is_exported(self):
        p = self.page()
        self.walk(p)                                                 # a log was saved, but this session has no position
        p.map_b.clicked.emit()
        self.assertEqual(p.map_info.text(), walkmap.NO_FIX)
        self.assertEqual((p.track, p.heard), ([], []))
        p.map.paintEvent(None)
        p.map.mousePressEvent(self.tap(450.0, 150.0))
        self.assertIsNone(p.chart.scrub)
        p.gpx.clicked.emit()
        self.assertIn("No GPS fix during this session, so there is nothing to put in a GPX file", p.detail.text())
        p.geojson.clicked.emit()
        self.assertEqual(len(ev.all_files()), 1)

    def test_the_map_goes_away_while_recording_and_a_saved_session_gets_its_own(self):
        p = self.page(GPS="on")
        self.walk(p)
        p.map_b.clicked.emit()
        p.start()
        self.assertFalse(p.map.isVisible() or p.map_b.isEnabled())
        self.assertTrue(p.notes.isVisible())
        p.stop()
        q = ev.build_page(self.win)
        q.showEvent(None)
        q.pick.activated.emit(q.pick.count() - 1)                    # the oldest: the walk
        self.assertEqual((len(q.track), len(q.heard)), (3, 1))

    def aircraft(self, *entries):
        with open(self.aircraft_file, "w") as f:
            json.dump({"now": self.now, "aircraft": list(entries)}, f)

    def test_aircraft_are_counted_while_the_receiver_is_already_writing_its_file(self):
        p = self.page(SDR="on")
        self.aircraft({"hex": "4ca123", "flight": "EIN123", "seen": 0.5, "lat": 53.4, "lon": -6.2, "seen_pos": 0.5},
                      {"hex": "4ca124", "seen": 3.0})
        p.showEvent(None)
        self.assertIn("<b>Aircraft</b>: how many aircraft the receiver that is already running hears", p.sources.text())
        p.start()
        self.seconds(6)
        os.remove(self.aircraft_file)                               # the receiver was stopped by its owner
        self.seconds(10)
        p.stop()
        samples = [r for r in self.lines() if r["k"] == "sample"]
        self.assertEqual([r.get("adsb") for r in samples], [{"heard": 2, "pos": 1}, {"heard": 2, "pos": 1}, None, None])
        texts = [r["text"] for r in self.lines() if r["k"] == "event"]
        self.assertIn("Aircraft dropped out: the aircraft receiver is not running", texts)
        with open(ev.files()[-1]) as f:
            saved = f.read()
        for private in ("4ca123", "EIN123", "53.4", "-6.2"):
            self.assertNotIn(private, saved)

    def test_aircraft_are_greyed_with_the_reason_when_the_rail_is_off_or_the_receiver_is_not_running(self):
        p = self.page(SDR="on")
        p.showEvent(None)
        self.assertIn("<b>Aircraft</b>: the aircraft receiver is not running", p.sources.text())
        self.aircraft({"hex": "4ca123", "seen": 0.5})
        p = self.page()
        p.showEvent(None)
        self.assertIn("<b>Aircraft</b>: the SDR rail is off", p.sources.text())
        p.start()
        self.seconds(6)
        p.stop()
        self.assertTrue(all("adsb" not in r for r in self.lines()))

    def test_before_any_session_the_row_of_sources_says_what_start_would_use(self):
        p = self.page()
        p.showEvent(None)
        self.assertIn("<b>GPS</b>: the GPS rail is off", p.sources.text())
        self.assertIn("<b>Wi-Fi</b>: ready (Start begins listening)", p.sources.text())
        self.assertEqual((self.win.bt.starts, self.win.survey.starts), (0, 0))
        self.assertEqual(ev.files(), [])


if __name__ == "__main__":
    unittest.main()
