The **aiov2_ctl GUI** is a lightweight system tray interface designed for *quick, low-friction control* of the AIO v2 board.

---

## Launch

```bash
aiov2_ctl --gui
```

The GUI runs as a background tray application and does not open a traditional window on launch.

If nothing appears immediately, check your system tray area.

---

## Tray Behaviour

### Right-click (Context Menu)

Right-clicking the tray icon opens the control menu.

From here you can:

- Toggle GPIO-controlled peripherals:
  - GPS
  - LoRa
  - SDR
  - USB
- Set which rails come on at boot (**Rails on boot**)
- Choose the boot device, SD or NVMe first (**Boot device**)
- See, start, stop or restart `meshtasticd` and `readsb` (**App Services**)
- See the current system power draw (battery-sourced)
- See which companion devices are plugged in
- Open the installer window (**Add or remove features…**)
- See whether an update is waiting, and install it
- Quit the GUI cleanly

Each toggle maps directly to a GPIO pin.  
There is no intermediate state or software abstraction — toggling is immediate.

### Rails on boot submenu

The right-click menu includes a **Rails on boot** submenu for per-feature boot preferences.

Important distinction:

- Main feature toggles = **live rail state now**
- **Rails on boot** toggles = **persisted boot preference**

So a feature may be OFF in the main live toggle while still set ON in boot preference (or vice versa), depending on current runtime state.

---

### Left-click (Status Window)

Left-clicking the tray icon opens a small floating status window near the cursor.

This window shows:

- Current power mode (battery / AC / mixed)
- Live power draw (W)
- Battery percentage
- GPIO states with checkboxes

The status window is:

- Read-only by default (aside from toggles)
- Auto-refreshed every second
- Non-modal and non-blocking

It is designed to be glanced at, not managed.

---

## Live Refresh Model

The GUI updates once per second using the same telemetry path as the CLI:

- Battery voltage
- Battery current
- Derived power (V × I)
- GPIO state via `pinctrl`

This ensures:

- CLI and GUI always agree
- No duplicated logic
- No hidden smoothing or averaging

If telemetry is unavailable, the GUI will show `n/a` rather than guessing.

When debugging status/source mismatches, compare:

```bash
pinctrl get <pin>
AIOV2_CTL_DEBUG=1 aiov2_ctl --status
```

---

## Autostart

You can enable automatic startup on login:

```bash
aiov2_ctl --autostart
```

This installs an XDG autostart entry at:

```text
~/.config/autostart/aiov2_ctl.desktop
```

Notes:

- Autostart runs **as the logged-in user**, not root
- A short delay is applied to avoid racing the desktop environment
- The GUI will refuse to start if already running

To disable autostart:

```bash
aiov2_ctl --no-autostart
```

---

## Single-instance Protection

The GUI enforces a single running instance with a lock file in `~/.cache/aiov2_ctl`. A lock left behind by a crash is noticed and does not stop the next start.

If you attempt to start it twice:

- The second instance exits immediately
- No duplicate tray icons appear
- No state corruption occurs

This is intentional and by design.

---

## Icons and Fallbacks

The tray icon is loaded in this order:

1. Installed PCB icon from `/usr/local/share/aiov2_ctl/img/`
2. Desktop theme system-monitor icon
3. Qt fallback system icon

This ensures the GUI remains usable even if assets are missing.

---

## Design Philosophy

The GUI is intentionally minimal.

It exists to answer three questions quickly:

1. What’s powered right now?
2. How much power is the system actually using?
3. Can I safely toggle this hardware on or off?

Anything more complex belongs in the CLI.

---

## Add or remove features

The tray's menu has "Add or remove features", which opens the installer window. It is also in the start menu as "AIO v2 Installer", and from a terminal:

```bash
aiov2_ctl --installer
```

Run it as yourself; it asks for root when it installs.

[![The installer window](img/install/gui-01-choose.webp)](img/install/gui-01-choose.webp)

Unticking a feature that is installed removes it. The plan says so before anything happens.

[![The plan when a feature is being removed](img/install/gui-04-plan-to-remove.webp)](img/install/gui-04-plan-to-remove.webp)

See [Installation](Installation) and [Uninstall](Uninstall).

## The Ops app

The tray is the small tool. The full-screen window is the Ops app: see [Ops App](Ops-App).

## If the tray does not appear

- `PyQt6 could not be imported` although `python3-pyqt6` is installed usually means a virtualenv Python is first on your `PATH` (the PyGPSClient installer can do this). Check `which python3`. The tool retries with `/usr/bin/python3` by itself.
- On GNOME the tray needs the AppIndicator extension: `sudo apt install gnome-shell-extension-appindicator`.
