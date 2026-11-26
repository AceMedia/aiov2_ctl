"""
The terminal drawer: a panel that slides up over the bottom of the app, so you never have to leave the app
(or full screen) to see what it has been doing or to run a command.

Two tabs. ACTIVITY is a short log of what the app just did (rails switched, scans, notifications). SHELL is a
small command line that runs in your own account: type a command, see its output. It has no full screen
programs (no top or nano); for those, WINDOW opens your system's own terminal (the one set by $TERMINAL or
Debian's x-terminal-emulator) next to the app, and CONSOLE swaps to the ops console as before.
The helpers at the top are pure so they can be tested without a screen.
"""
import re
import time

ACTIVITY_LIMIT = 300
SHELL_MARK = "__OPS_DONE__"
SHELL_LIMIT = 200_000
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


class ActivityLog:
    """A short, bounded record of what the app has done. Newest last."""

    def __init__(self, limit=ACTIVITY_LIMIT):
        self.limit = limit
        self.entries = []
        self.listeners = []

    def add(self, text, level="info", now=None):
        text = " ".join(str(text).split())
        if not text:
            return
        entry = (time.strftime("%H:%M:%S", time.localtime(now)), level, text)
        self.entries = (self.entries + [entry])[-self.limit:]
        for fn in list(self.listeners):
            try:
                fn(entry)
            except Exception:
                pass

    def lines(self, count=None):
        out = [f"{t}  {text}" for t, _l, text in self.entries]
        return out[-count:] if count else out


def strip_ansi(text):
    return _ANSI.sub("", text or "")


def clean_output(text):
    """Terminal output made fit to show: no colour codes, no NULs, plain line ends, backspaces applied."""
    text = strip_ansi(text).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    while "\x08" in text:
        text = re.sub(r"[^\x08]\x08", "", text, count=1) if re.search(r"[^\x08]\x08", text) else text.replace("\x08", "")
    return text


def shell_input(command):
    """The bytes to send the shell for one command, with a marker so we can tell when it has finished."""
    command = (command or "").replace("\r", " ").replace("\n", " ").strip()
    return f"{command}\necho {SHELL_MARK} $?\n".encode()


class ShellFilter:
    """Turns raw shell output into what to show, hiding the end marker and reporting exit codes."""

    def __init__(self):
        self.partial = ""

    def feed(self, raw):
        """(text to show, [exit codes of commands that finished])."""
        text = self.partial + clean_output(raw)
        *lines, self.partial = text.split("\n")
        shown, codes = [], []
        for line in lines:
            if line.startswith(SHELL_MARK):
                try:
                    codes.append(int(line.split()[1]))
                except (IndexError, ValueError):
                    codes.append(-1)
            else:
                shown.append(line)
        return "".join(l + "\n" for l in shown), codes


class History:
    """Up and down through earlier commands, like any shell."""

    def __init__(self, limit=100):
        self.items, self.pos, self.limit = [], 0, limit

    def add(self, command):
        command = (command or "").strip()
        if command and (not self.items or self.items[-1] != command):
            self.items = (self.items + [command])[-self.limit:]
        self.pos = len(self.items)

    def previous(self):
        if not self.items:
            return ""
        self.pos = max(0, self.pos - 1)
        return self.items[self.pos]

    def next(self):
        if not self.items:
            return ""
        self.pos = min(len(self.items), self.pos + 1)
        return "" if self.pos >= len(self.items) else self.items[self.pos]


def drawer_height(window_height):
    """About 42 percent of the window, never tiny and never most of it."""
    return max(200, min(int(window_height * 0.42), 380))


def build_drawer(win):
    from PyQt6.QtCore import QEasingCurve, QProcess, QPropertyAnimation, QRect, Qt
    from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QStackedWidget,
                                 QVBoxLayout, QWidget)

    FONT = 'font-family: "Spleen 8x16"; font-size: 16px;'

    class Input(QLineEdit):
        def __init__(self, history):
            super().__init__()
            self.history = history

        def keyPressEvent(self, event):
            if event.key() == Qt.Key.Key_Up:
                self.setText(self.history.previous())
            elif event.key() == Qt.Key.Key_Down:
                self.setText(self.history.next())
            else:
                super().keyPressEvent(event)

    class Drawer(QFrame):
        def __init__(self):
            super().__init__(win)
            self.setObjectName("drawer")
            self.hide()
            self.shell, self.filter, self.history = None, ShellFilter(), History()
            self.anim = QPropertyAnimation(self, b"geometry", self)
            self.anim.setDuration(150)
            self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(10, 8, 10, 8)
            bar = QHBoxLayout()
            self.tabs = {}
            for i, name in enumerate(("ACTIVITY", "SHELL")):
                b = QPushButton(name)
                b.setCheckable(True)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setStyleSheet(FONT)
                b.clicked.connect(lambda _=False, i=i: self.show_tab(i))
                bar.addWidget(b)
                self.tabs[i] = b
            bar.addStretch(1)
            for text, slot, tip in (("WINDOW", self.open_window, "Open your system terminal next to the app"),
                                    ("CONSOLE", win.open_terminal, "Swap to the ops console"),
                                    ("CLOSE", self.close_drawer, "Close (Esc)")):
                b = QPushButton(text)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setStyleSheet(FONT)
                b.setToolTip(tip)
                b.clicked.connect(slot)
                bar.addWidget(b)
            lay.addLayout(bar)
            self.stack = QStackedWidget()
            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setStyleSheet(FONT)
            self.stack.addWidget(self.log_view)
            shell = QWidget()
            sl = QVBoxLayout(shell)
            sl.setContentsMargins(0, 0, 0, 0)
            self.out = QPlainTextEdit()
            self.out.setReadOnly(True)
            self.out.setMaximumBlockCount(2000)
            self.out.setStyleSheet(FONT)
            row = QHBoxLayout()
            self.inp = Input(self.history)
            self.inp.setPlaceholderText("Type a command and press Enter (Up and Down for history)")
            self.inp.setStyleSheet(FONT)
            self.inp.returnPressed.connect(self.run_command)
            stop = QPushButton("STOP")
            stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            stop.setStyleSheet(FONT)
            stop.setToolTip("Stop whatever is running and start a fresh shell")
            stop.clicked.connect(self.restart_shell)
            row.addWidget(self.inp, 1)
            row.addWidget(stop)
            sl.addWidget(self.out, 1)
            sl.addLayout(row)
            self.stack.addWidget(shell)
            lay.addWidget(self.stack, 1)
            win.activity.listeners.append(self.on_activity)
            from . import style
            style.crisp_fonts(self)
            self.show_tab(0)

        # ---- showing and hiding
        def target_rect(self):
            h = drawer_height(win.height())
            return QRect(12, win.height() - h - 40, win.width() - 24, h)

        def open_drawer(self):
            rect = self.target_rect()
            self.setGeometry(rect.x(), win.height(), rect.width(), rect.height())
            self.show()
            self.raise_()
            self.anim.stop()
            self.anim.setStartValue(self.geometry())
            self.anim.setEndValue(rect)
            self.anim.start()
            self.refresh_log()
            if self.stack.currentIndex() == 1:
                self.inp.setFocus()

        def close_drawer(self):
            self.anim.stop()
            self.hide()
            win.setFocus()

        def toggle(self):
            if self.isVisible():
                self.close_drawer()
            else:
                self.open_drawer()

        def reposition(self):
            if self.isVisible() and self.anim.state() == QPropertyAnimation.State.Stopped:
                self.setGeometry(self.target_rect())

        def show_tab(self, i):
            self.stack.setCurrentIndex(i)
            for k, b in self.tabs.items():
                b.setChecked(k == i)
            if i == 1:
                self.ensure_shell()
                self.inp.setFocus()

        # ---- activity
        def refresh_log(self):
            self.log_view.setPlainText("\n".join(win.activity.lines()))
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

        def on_activity(self, entry):
            if self.isVisible() and self.stack.currentIndex() == 0:
                self.log_view.appendPlainText(f"{entry[0]}  {entry[2]}")

        # ---- shell
        def ensure_shell(self):
            if self.shell is not None:
                return
            import os
            self.shell = QProcess(self)
            self.shell.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            self.shell.setWorkingDirectory(os.path.expanduser("~"))
            self.shell.readyRead.connect(self.read_shell)
            self.shell.start("bash", ["--noprofile", "--norc"])
            self.out.appendPlainText("Shell in your home folder. Programs that need a full screen (top, nano) will not work here:\n"
                                     "use WINDOW for your system terminal.\n")

        def read_shell(self):
            raw = bytes(self.shell.readAll()).decode(errors="replace")
            text, codes = self.filter.feed(raw)
            if text:
                self.out.moveCursor(self.out.textCursor().MoveOperation.End)
                self.out.insertPlainText(text)
            for code in codes:
                if code:
                    self.out.appendPlainText(f"[exit {code}]")
            self.out.verticalScrollBar().setValue(self.out.verticalScrollBar().maximum())

        def run_command(self):
            command = self.inp.text().strip()
            if not command:
                return
            self.history.add(command)
            self.inp.clear()
            self.ensure_shell()
            self.out.moveCursor(self.out.textCursor().MoveOperation.End)
            self.out.insertPlainText(f"$ {command}\n")
            self.shell.write(shell_input(command))
            win.activity.add(f"shell: {command[:60]}")

        def restart_shell(self):
            if self.shell is not None:
                self.shell.kill()
                self.shell.waitForFinished(500)
                self.shell.deleteLater()
                self.shell = None
            self.filter = ShellFilter()
            self.out.appendPlainText("[shell restarted]")
            self.ensure_shell()

        def open_window(self):
            from ops_agent import handoff
            argv = handoff.plain_terminal_argv(os.path.expanduser("~"))
            if not argv or not handoff.launch_detached(argv):
                win.say("No terminal program found. Set $TERMINAL or install one (apt install lxterminal).")
            else:
                win.activity.add("opened a terminal window")

        def shutdown(self):
            if self.shell is not None:
                self.shell.kill()
                self.shell.waitForFinished(300)

    return Drawer()


import os  # noqa: E402  (used by the Qt part above)
