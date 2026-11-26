"""
One shared connection to the mesh (the Meshtastic engine), kept for as long as anything wants it.

Without this the LoRa tab opened the connection when you looked at it and closed it when you left, so the app
could never tell you a message had arrived while you were on another tab. Now the hub keeps the connection
while the LoRa rail is on and either the LoRa tab is showing or you want mesh notifications. Every two seconds
it looks at what the engine has heard and turns new messages, check-ins and (if you asked) new nodes into
notifications. It never sends anything; sending stays behind the Allow sending switch in the LoRa tab.

Every holder is told, through the same callback it registered with `acquire`, whenever the engine it was given
stops being usable (the rail went off, the connection died, or it was replaced) so nothing is left holding a
closed connection.
"""
import subprocess
import threading
import time

from . import lora_panel, notify

POLL_MS = 2000
RETRY_SECONDS = 60
POLL_FAILURES_BEFORE_DROP = 3        # a few bad polls in a row means the connection is really dead, not a blip


def start_engine(rail):
    """Open the connection. Runs in a thread. Returns {'hint': text, 'lora': engine or None}."""
    result = {"hint": "", "lora": None}
    try:
        from ops_agent import bootstrap
        ok = bootstrap._ensure_meshtastic_import()
        bootstrap._apply_default_meshtastic_tcp({"LORA": True})
        active = subprocess.run(["systemctl", "is-active", "meshtasticd"], text=True,
                                capture_output=True, timeout=5).stdout.strip() == "active"
        result["hint"] = lora_panel.hint(ok, active, rail)
        if not result["hint"]:
            from ops_agent.lora import LoRa
            engine = LoRa()
            engine.connect()
            result["lora"] = engine
    except Exception as e:
        result["hint"] = f"LoRa engine unavailable: {e}"
    return result


def build_hub(win):
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal

    class Signals(QObject):
        ready = pyqtSignal(object)

    class Hub:
        def __init__(self):
            self.engine, self.starting = None, False
            self.users = {}                       # who -> callback wanting the result
            self.seen, self.known = None, set()
            self.next_try = 0.0
            self.last_nodes = 0.0
            self.poll_failures = 0
            self.signals = Signals()
            self.signals.ready.connect(self.on_ready)
            self.timer = QTimer()
            self.timer.timeout.connect(self.poll)

        def rail_on(self):
            return ((win.snap or {}).get("rails") or {}).get("LORA") == "on"

        def wanted(self):
            cfg = win.notifier.cfg
            return self.rail_on() and (bool(self.users) or bool(cfg.get("mesh") or cfg.get("nodes")))

        def sync(self):
            """Start or stop the connection to match what is wanted right now."""
            if self.wanted():
                if self.engine is None and not self.starting and time.time() >= self.next_try:
                    self.starting = True
                    rail = "on"
                    threading.Thread(target=lambda: self.signals.ready.emit(start_engine(rail)), daemon=True).start()
            elif self.engine is not None:
                self.stop()

        def acquire(self, who, callback):
            self.users[who] = callback
            if self.engine is not None:
                QTimer.singleShot(0, lambda: callback({"hint": "", "lora": self.engine}))
            else:
                self.next_try = 0.0
                self.sync()
                if not self.starting and not self.rail_on():
                    callback({"hint": lora_panel.hint(True, True, "off"), "lora": None})

        def release(self, who):
            self.users.pop(who, None)
            self.sync()

        def on_ready(self, result):
            self.starting = False
            engine = result.get("lora")
            if engine is None:
                self.next_try = time.time() + RETRY_SECONDS
            elif not self.wanted():
                # whatever wanted this connection let go of it while it was still starting up: close it straight
                # away rather than leaving it open and unused.
                self._close(engine)
                return
            else:
                self.engine, self.seen, self.poll_failures = engine, None, 0
                self.known = set(str(k) for k in (engine.snapshot().get("node_info") or {}))
                self.timer.start(POLL_MS)
            for cb in list(self.users.values()):
                cb(result)

        def stop(self, hint=""):
            """Drop the connection and tell every holder: it must not go on using an engine this closed."""
            engine = self.engine
            self._close(engine)
            for cb in list(self.users.values()):
                cb({"hint": hint, "lora": None})

        def _close(self, engine):
            self.engine = None
            self.timer.stop()
            self.seen = None
            if engine:
                try:
                    engine.active = False
                    if engine.interface:
                        engine.interface.close()
                except Exception:
                    pass

        def poll(self):
            e = self.engine
            if e is None:
                return
            try:
                if not self.users and time.time() - self.last_nodes > 30:
                    e.refresh_nodes()
                    self.last_nodes = time.time()
                events, self.seen = notify.mesh_events(e.snapshot().get("activities"), self.seen, self.known)
            except Exception:
                self.poll_failures += 1
                if self.poll_failures >= POLL_FAILURES_BEFORE_DROP:
                    # several polls in a row have failed: the connection is dead, not just slow. Drop it and let
                    # sync() reconnect, instead of sitting on a connection that will never work again.
                    self.poll_failures = 0
                    self.next_try = 0.0
                    self.stop(hint="The mesh connection was lost. Reconnecting...")
                return
            self.poll_failures = 0
            for source, title, body, level in events:
                win.notifier.add(source, title, body, level)

    return Hub()
