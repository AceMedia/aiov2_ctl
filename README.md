# aiov2_ctl

A lightweight **power and feature control client** for the HackerGadgets **AIO v2** board (GPIO-based fork).

This tool controls onboard hardware (GPS, LoRa, SDR, USB power rails) via **direct GPIO access using `pinctrl`**, and supports both **CLI** and **system tray GUI** modes.

---

## What this tool does

`aiov2_ctl` directly toggles GPIO pins mapped to hardware enable lines in order to:

- Enable / disable onboard hardware modules
- Query current on/off state
- Monitor overall board power usage
- Provide a tray-based GUI for quick toggling
- Optionally auto-start the GUI on login (XDG desktops)

GPIO state reporting model:

- Runtime state is **truth-first** from `pinctrl get` level parsing (`hi`/`lo`)
- If level is `--` (not driven), state falls back to explicit per-pin boot defaults
- No global GPIO inference heuristics are used

---

## Install

On a fresh uConsole (Debian or Raspberry Pi OS), as yourself, not as root:

```bash
curl -fsSLO https://raw.githubusercontent.com/AceMedia/aiov2_ctl/main/install.sh
sh install.sh
```

`install.sh` clones this repository into `~/aiov2_ctl-ace` (or updates the checkout an earlier install used), checks `aiov2_ctl.py` is a whole Python file, says what it is about to run, and then runs `sudo python3 aiov2_ctl.py --install --all`. The checkout is where the tray's icons come from and what `aiov2_ctl --update` pulls. A folder that is some other project, or a checkout with your own changes, is never changed. Without `git`, or if the clone fails, it downloads the one program file instead. Anything you put after `sh install.sh` is passed on to `--install` in place of `--all`.

From a checkout, or once it is installed, the same installer is:

```bash
aiov2_ctl --features                       # what can be installed, what is on by default, what is here now
aiov2_ctl --install --dry-run              # the plan, in plain words; changes nothing and needs no root
sudo aiov2_ctl --install --pick            # tick the features you want on screen, then see the plan
sudo aiov2_ctl --install                   # the usual set
sudo aiov2_ctl --install --all             # everything, including the aircraft receiver and tray at login
sudo aiov2_ctl --install --only core,tray  # just these (and what they need)
sudo aiov2_ctl --install --without app,console
sudo aiov2_ctl --install --yes             # do not ask; needed when there is no terminal to ask on
```

| Feature | Default | What it brings |
| --- | --- | --- |
| `core` | always | the `aiov2_ctl` command, bash completion, the rail boot service, the password-free rules |
| `tray` | on | the tray (`--gui`), its icons and start-menu entry, `python3-pyqt6` |
| `app` | on | the uConsole Ops app, fetched to `~/ops-agentic`, with its start-menu shortcut, `python3-pyqt6`, `fonts-spleen` |
| `console` | on | the Ops console (`ops.py`) from the same checkout, `python3-textual`, `python3-numpy` |
| `gps` | on | `gpsd` |
| `mesh` | on | `meshtasticd` |
| `sdr` | on | `rtl-sdr` and the readsb tuner recovery |
| `adsb` | off | `readsb` |
| `listening` | on | `iw` and the rules for `--wifi-listen`, `--wifi-survey` and `--bt-scan` |
| `flipper` | on | the udev rule for a Flipper Zero or ESP32 board |
| `autostart` | off | the tray starts when you log in |

What it does and does not do:

- It prints the plan and asks once before changing anything. It ends with a line per feature: installed, already there, or failed and why. One feature failing does not stop the others.
- It never switches a rail on or starts a radio. Nothing is made to start at boot except the tool's own rail boot service and SDR check (as install has always done) and, if you pick `autostart`, the tray. A service that a package enables as it installs (`gpsd`, `meshtasticd`, `readsb`) is switched back off when this install is what added it; one that was already there is left exactly as it is.
- Hardware that is not found (a rail that is off, no Flipper plugged in) does not stop a feature being installed: the plan says what was not found.
- The ops app lives in the home of the user who ran `sudo`, and every `git` and `python3` command in that home runs as that user, never as root. An existing checkout is updated with `git pull --ff-only`; one with your own changes, or one that has gone its own way, is reported and left alone.
- The ops app repository is private. If the clone is refused, sign in with `gh auth login` (then `gh auth setup-git`), or clone it by hand to `~/ops-agentic`, and run the install again.
- The console's remaining Python packages (`meshtastic`, and `textual` or `numpy` where apt does not have them) are installed by the console itself on its first run, as you. The installer never runs pip as root.
- If `install.sh` had to fall back to downloading the one program file (no `git`, or the clone failed), there is no checkout of this tool: the tray uses a stock icon and `aiov2_ctl --update` has nothing to pull from. Install `git` and run `install.sh` again.

---

## Requirements

- HackerGadgets uConsole **AIO v2 Upgrade Kit**  
  https://hackergadgets.com/products/uconsole-upgrade-kit?variant=47038702682286
- uConsole running **Debian / Raspberry Pi OS** (Bookworm or Trixie recommended)
- Python **3.9+**
- `pinctrl` available on the system (used for direct GPIO control)
- Desktop environment with system tray support (for GUI mode)

---

## 1) System dependencies

Install required system packages:

```bash
sudo apt update
sudo apt install -y python3 python3-pyqt6 git
```

---

## 2) Install aiov2_ctl (system-wide)

The recommended install location is `/usr/local/bin`.

Clone the repository anywhere (e.g. your home directory), then install:

```bash
git clone https://github.com/hackergadgets/aiov2_ctl.git
cd aiov2_ctl

sudo python3 ./aiov2_ctl.py --install
```

Sanity check:

```bash
aiov2_ctl
aiov2_ctl --status
```

Install also enables the rail boot apply service:

- `aiov2-rails-boot.service`
- Applies configured rail boot states at startup

and the readsb SDR recovery:

- `/etc/systemd/system/readsb.service.d/10-aiov2-sdr-recovery.conf` — waits for the RTL-SDR before readsb starts, and power cycles the SDR rail after readsb fails (skipped if the rail is off or another app holds the SDR)
- `aiov2-sdr-watchdog.timer` — every minute, recovers readsb if it is running but no longer receiving samples
- Keep the SDR powered at boot for ADS-B with `sudo aiov2_ctl --boot-rail SDR on`

---

## 3) Optional: HackerGadgets AIO Companion Apps

This step is **optional** and not required for basic GPIO or GUI usage.

### Install preconfigured AIO apps

```bash
sudo aiov2_ctl --add-apps
```

Installs and configures:

- **`hackergadgets-uconsole-aio-board`** — Core AIO v2 integration (GPIO, power rails, RTC support, services, and uConsole-specific configuration)
- **`meshtastic-mui`** — Meshtastic graphical UI for LoRa / Meshtastic devices (depends on the AIO package)
- **`sdrpp-brown`** — Preconfigured SDR++ build for the uConsole (RF scanning/listening via SDR)
- **`tar1090`** — ADS-B aircraft tracking web UI (visualises planes from your SDR feed)
- **`pygpsclient`** — GPS monitoring and diagnostics GUI (position, satellites, NMEA data), preset to the GPS UART (`/dev/serial0`, 9600 baud)

This pulls in supporting services (RTC, GPIO helpers, and desktop menu entries) used by the HackerGadgets AIO ecosystem.

---

### Remove AIO apps (cleanup / testing)

```bash
sudo aiov2_ctl --remove-apps
```

Removes the above packages and runs `apt autoremove`.  
Useful for testing reinstallation or returning to a minimal setup.

---

### Sync system time to RTC

```bash
sudo aiov2_ctl --sync-rtc
```

Writes the **current system time** to the hardware RTC using `hwclock -w`.

> Only run this after confirming system time is correct (e.g. via NTP).

---

### RTL-SDR bias tee for readsb

```bash
sudo aiov2_ctl --sdr-biastee on
sudo aiov2_ctl --sdr-biastee off
aiov2_ctl --sdr-biastee status
```

Powers the SDR antenna input (bias tee) every time readsb starts, for an active antenna or inline LNA. Debian/Kali readsb builds ignore `--enable-biastee` on RTL-SDRs, so this adds `readsb.service.d/20-aiov2-sdr-biastee.conf`, which runs `rtl_biast -b 1` before readsb opens the tuner (needs the `rtl-sdr` package).

> Only enable it with an antenna or LNA that expects DC power. The bias tee stays on until the SDR loses power, so turn it off before connecting a passive antenna for other SDR apps.

---

### USB WiFi monitor mode without losing the internet

```bash
sudo aiov2_ctl --wifi-monitor on
sudo aiov2_ctl --wifi-monitor off
aiov2_ctl --wifi-monitor status
```

`airmon-ng check kill` stops NetworkManager and wpa_supplicant, which also takes the onboard WiFi (`wlan0`) offline. `--wifi-monitor on` adds `/etc/NetworkManager/conf.d/99-aiov2-usb-wifi-unmanaged.conf`, telling NetworkManager to leave the USB WiFi (MT7961, `mt7921u`) alone, and reloads its configuration without dropping connections. Then skip `check kill`:

```bash
sudo airmon-ng start wlan1
sudo airodump-ng wlan1mon
sudo airmon-ng stop wlan1mon
```

- `airmon-ng check` still lists NetworkManager; it no longer touches `wlan1`, so ignore it
- While on, NetworkManager will not connect `wlan1` to networks; `--wifi-monitor off` hands it back
- The USB rail powers this adapter; keep it on at boot with `sudo aiov2_ctl --boot-rail USB on`

---

## 4) CLI usage

Show current GPIO state:

```bash
aiov2_ctl
```

Show detailed status (including battery / power rail info):

```bash
aiov2_ctl --status
```

Enable or disable a feature:

```bash
aiov2_ctl <FEATURE> <on|off>
```

Supported features:

- `GPS`
- `LORA`
- `SDR`
- `USB`

Examples:

```bash
aiov2_ctl GPS on
aiov2_ctl LORA off
aiov2_ctl SDR on
```

### Rail boot-state commands

Configure per-feature rail state to apply at boot:

```bash
aiov2_ctl --boot-rail <FEATURE> on
aiov2_ctl --boot-rail <FEATURE> off
aiov2_ctl --boot-rail <FEATURE> status
aiov2_ctl --boot-rails-status
```

Examples:

```bash
aiov2_ctl --boot-rail SDR on
aiov2_ctl --boot-rail LORA off
aiov2_ctl --boot-rails-status
```

Notes:

- Boot rail settings are applied by `aiov2-rails-boot.service`
- `--boot-rail ... on|off` may require sudo escalation depending on context

### Boot device (EEPROM boot order)

Control whether the CM5 bootloader tries **SD/eMMC** or **NVMe** first:

```bash
aiov2_ctl --boot-device status
sudo aiov2_ctl --boot-device sd
sudo aiov2_ctl --boot-device nvme
sudo aiov2_ctl --boot-device toggle
```

This edits the bootloader `BOOT_ORDER` via `rpi-eeprom-config --apply`:

- `sd`   → `0xf61` (SD/eMMC → NVMe → repeat)
- `nvme` → `0xf16` (NVMe → SD/eMMC → repeat)

Notes:

- Requires `rpi-eeprom` (`sudo apt install rpi-eeprom`)
- Changes take effect on **next reboot**; the tool offers to reboot when run interactively
- `status` works without sudo; `sd`/`nvme`/`toggle` escalate automatically
- A no-op (already in the requested mode) is detected and skipped

---

## 5) Power monitoring

Live power monitor (Ctrl+C to exit):

```bash
aiov2_ctl --power
```

Compact live GPIO + power line (single-line view):

```bash
aiov2_ctl --watch
```

---

## 6) GUI mode (system tray)

Start the tray-based GUI:

```bash
aiov2_ctl --gui
```

### Behaviour

- **Left-click**: opens a small status window (Wayland-safe)
- **Right-click**: opens tray menu to toggle hardware
- **Right-click** also includes a **Rails on boot** submenu for per-feature boot preferences
- Power usage is updated once per second
- GUI must **not** be run as root

GUI menu distinction:

- Main feature toggles = **live rail state now**
- `Rails on boot` toggles = **persisted boot preference**

---

## 7) Autostart GUI on login (recommended)

For desktop environments that support **XDG autostart** (LXQt, XFCE, GNOME, etc.), this is handled automatically.

### Enable autostart

```bash
aiov2_ctl --autostart
```

Creates:

```
~/.config/autostart/aiov2_ctl.desktop
```

### Disable autostart

```bash
aiov2_ctl --no-autostart
```

### Notes

- Autostart is **per-user**, not system-wide
- Never run autostart commands as root
- Uses `/usr/local/bin/aiov2_ctl --gui`
- Includes a small startup delay to allow GPIO and desktop services to settle

---

## 8) Updating aiov2_ctl

Pull the latest version and reinstall automatically:

```bash
aiov2_ctl --update
```

Behaviour:

- Git operations are **never** run as root
- The tool escalates **only** for the install step
- Clean exit if already up to date
- Update path re-runs install, including boot rail service install/enable

---

## 9) Running the tests

The tests mock all GPIO, sysfs and systemd access, so they run on any machine without touching the board:

```bash
python3 -m unittest discover -s tests
```

`python3 -m pytest tests` works too if pytest is installed.

---

## Health check (uConsole)

```bash
aiov2_ctl --doctor
```

A read only check that changes nothing. It looks at:

- **RTC**: `dtparam=rtc=off` is meant to be paired with the AIO board's `i2c-rtc,pcf85063a` overlay, because the CM5's own clock has no battery. It checks both are there and that the clock registered. A power on reset message at boot usually means the CR1220 is flat, loose or missing.
- **Kernel**: that a `clockworkpi-kernel` or `akrex-kernel` package is installed, and (with a dry run of `apt-get -s full-upgrade`) that an upgrade would not remove a kernel or firmware package. Some upgrades have left uConsoles unbootable. Never run `rpi-update` on these.
- **EEPROM**: the bootloader date. A CM5 Lite wants one newer than 2025-01-06. It reports only and never writes to the EEPROM.
- **config.txt**: known bad settings, for example `dtparam=spi=on` disables the backlight on the CM5 (use `dtoverlay=spi1-1cs`).
- **Battery and power**: whether the gauge percentage and the cell voltage agree, and any undervoltage since boot.
- **Storage**: a read only root filesystem or filesystem and card errors in this boot's log.
- **Audio**: a note about the harmless `alsa_restore_std` udev warning from alsa-utils 1.2.14-1 (Debian bug 1093057).

Each line is `OK`, `INFO` or `WARN`. The checks come from ClockworkPi forum posts, the Raspberry Pi documentation and the Debian bug tracker, so treat them as advice, not gospel.

## Companion devices (Flipper Zero, ESP32 boards)

Detection only for now. Nothing is opened, written to or reset.

```bash
aiov2_ctl --companions
```
Looks on the USB bus for a Flipper Zero (`0483:5740`) and ESP32 style boards (Espressif native USB, plus a hint for generic CP210x/CH340 chips, which are reported as "possible"). It shows the serial port and warns if the USB rail is off or if your user cannot open the port. The same line appears in `--status` and in the tray menu.

`--install` adds `/etc/udev/rules.d/99-aiov2-companions.rules`, which gives the logged in user access to the port with `uaccess` (no world writable devices) and tells ModemManager to leave it alone. Replug the device afterwards. The rule also adds stable names, `/dev/aiov2-flipper` and `/dev/aiov2-esp32`, so scripts do not have to guess between `ttyACM0` and `ttyACM1`.

`aiov2_ctl --companions --json` prints the same information for other tools to read, including the USB rail state (`on`, `off`, or `null` if it cannot be read). If a Flipper or Marauder command cannot find a device and the rail is off, the error says so.

Captures from the Flipper (`get REMOTE` with no destination) and Marauder scans both go to `~/aiov2-captures` (folder 700, files 600).

One thing to watch: the ops console looks for a LoRa radio on `ttyACM0`, `ttyUSB0` and `ttyACM1`. A Flipper plugged in first can take `ttyACM0`, so use the `/dev/aiov2-*` names for the companions and check `--companions` if LoRa will not connect.

### Flipper basics (read mostly)

```bash
aiov2_ctl --flipper info
aiov2_ctl --flipper ls [/ext]
aiov2_ctl --flipper apps
aiov2_ctl --flipper open <APP>
```
Talks to the Flipper's own serial CLI using only the standard library, so nothing extra to install. `info` shows hardware, firmware and battery, `ls` lists files (default is the SD card), `apps` lists what it can launch and `open` starts one. Add `--port /dev/ttyACMx` to pick a port. Paths and app names are checked before they reach the Flipper, so they cannot carry a second command. Close qFlipper first: the port is opened exclusively.

File transfer uses the Flipper's RPC protocol with a small built-in client (no protobuf package needed):

```bash
aiov2_ctl --flipper put ./payload.sub /ext/subghz/payload.sub [--force]
aiov2_ctl --flipper get /ext/subghz/payload.sub ./copy.sub
aiov2_ctl --flipper get /ext/subghz/payload.sub    # saves under ~/aiov2-captures/flipper/
aiov2_ctl --flipper mkdir /ext/mine
```

Transfers only touch the SD card (`/ext`), never the Flipper's internal storage. `put` refuses to overwrite without `--force`, checks the MD5 afterwards and reports a mismatch, `get` never overwrites a local file and saves with mode 600. Files are limited to 64 MiB. The RPC session is always ended, even after an error.

The protocol messages follow the official `flipperzero-protobuf` definitions. Checked on a real Flipper (Unleashed `unlshd-095`): `mkdir`, a multi chunk `put` with the MD5 check, the overwrite refusal and `get` all work.

These commands follow the published CLI but have not been checked on a real Flipper yet.

### ESP32 Marauder (passive only)

```bash
aiov2_ctl --marauder info
aiov2_ctl --marauder scan [all|beacon] [--seconds N]
aiov2_ctl --marauder list [aps|stations]
aiov2_ctl --marauder channel [N]
aiov2_ctl --marauder stop
```
Drives the Marauder firmware's serial command line (115200 baud) on an ESP32 board, either plugged in directly or through the Flipper's USB-UART Bridge app. `scan` streams what the board hears, saves it to `~/aiov2-captures` (folder 700, files 600) and always sends `stopscan` afterwards, even if you hit Ctrl+C.

Only a short list of passive commands is built in. There is no way to send attack, spoofing or portal commands from here. The log can contain SSIDs and MAC addresses, so treat it as private. This is for looking at your own networks and equipment, and the law where you live applies.

Checked on a real Flipper WiFi dev board running Marauder v1.21.0 through the Flipper's USB-UART Bridge app (GPIO menu, USART, 115200). A plain `\n` line ending works. Scans need a little time: 10 seconds heard nothing, 30 seconds (the default) found dozens of access points and stations. While the bridge app is running the Flipper's own CLI is not available, so `--flipper` commands will not work until you leave the app. The captured logs contain the names and MAC addresses of your neighbours' networks, so they are kept private (mode 600).

A Flipper charging from a uConsole port draws from the battery, so keep an eye on that until the revised board is in. Future phases (RPC, Marauder) are planned in the issue tracker.

---

### Wi-Fi radios and scan (passive only)

```bash
aiov2_ctl --wifi radios [--json]
aiov2_ctl --wifi scan [--iface wlan0|wlan1] [--json] [--save]
```
`radios` lists the Wi-Fi radios from sysfs: `wlan0` is the onboard Broadcom (`onboard`, brcmfmac), `wlan1` is the MediaTek MT7921 on the AIO board's internal USB (`internal-usb`, mt7921u, 0e8d:7961), and any other USB adapter shows as `external-usb`. `scan` does an ordinary managed mode scan on each radio through NetworkManager (`nmcli`), so it needs no root and no sudoers rule. If your Flipper's Wi-Fi dev board is running the USB-UART Bridge app and answers, its access points are added as a third radio called `flipper-marauder`. With no Flipper plugged in, that port is never opened.

`--json` prints `{"radios": [...], "networks": [...], "scanned_at": "..."}` with one entry per network and radio (name, bssid, channel, freq_mhz, signal_dbm, security, radio). Hidden networks have an empty `ssid`. NetworkManager only gives a 0 to 100 signal figure, so `signal_dbm` is an estimate worked out from it. The Marauder list has no BSSID or security information, so those show as empty and `unknown`. `--save` writes the JSON to `~/aiov2-captures` (folder 700, file 600, never overwrites). Nothing here deauthenticates, injects or spoofs; the saved file contains your neighbours' network names, so keep it private.

## Privileges and updates

- Anything run as root only ever executes a copy that root alone can modify (the installed `/usr/local/bin/aiov2_ctl`). If you run a boot-rail or similar command from a user writable checkout without installing first, it refuses and tells you to install.
- `aiov2_ctl --update` fetches first, shows the incoming commits and diffstat, and asks before it runs the new code as root. Use `--update --yes` to skip the question. Set `"require_signed_updates": true` in the system config to refuse any update whose newest commit is not validly signed.
- `sudo aiov2_ctl --setup-sudoers` is optional. It adds `/etc/sudoers.d/aiov2_ctl` so the tray can change boot rails, boot device and the meshtasticd/readsb services without a password. The rules are exact commands with no wildcards, are validated with `visudo -cf` before being written, and `sudo aiov2_ctl --setup-sudoers --remove` takes them away.
- `--remove-apps` no longer runs `apt autoremove -y`. It shows apt's list and asks, or skips it when not interactive.

---

## Safety notes

- GPIO writes happen immediately
- Assumes exclusive control of AIO v2 GPIO pins
- Battery telemetry comes directly from kernel `power_supply`
- Power deltas below **~0.05 W** are considered noise

State source visibility:

- Use `AIOV2_CTL_DEBUG=1 aiov2_ctl --status` to show source labels:
  - `(pinctrl)` for direct `hi/lo`
  - `(boot_default)` for `--` fallback
  - `(unknown)` for unparseable/failed reads

Fresh install defaults:

- The boot service sets every rail to **off** until you choose otherwise with `--boot-rail <FEATURE> on`. This includes SDR, even though the SDR rail is on by default in hardware before Linux boots.
- Boot settings are read from the system config (`/usr/local/share/aiov2_ctl/config.json`), so set them with sudo. If the tool has to fall back to your user config it will warn you, because the boot service will not see it.

Troubleshooting the GUI:

- `PyQt6 could not be imported` even though `python3-pyqt6` is installed usually means a virtualenv Python is first on your `PATH` (the PyGPSClient installer can do this). Check `which python3`. The tool now retries with `/usr/bin/python3` automatically.
- On GNOME (Ubuntu) the tray needs the AppIndicator extension: `sudo apt install gnome-shell-extension-appindicator`.

Maintained for **HackerGadgets uConsole AIO v2** users.

## Wi-Fi monitor mode for Sense (passive)

```
sudo aiov2_ctl --wifi-listen --iface wlan1 --channel 6 --seconds 60 --json
sudo aiov2_ctl --wifi-listen --restore          # put the radio back to normal if anything went wrong
```

Puts a Wi-Fi radio into monitor mode, listens to the traffic in the air on one channel and prints, once a second, how many frames it heard, the average signal strength and the signal per sender (`{"t", "frames", "rssi", "links": {mac: [count, dbm]}}`). It never transmits, refuses a radio that is connected to a network, and always puts the radio back (managed, NetworkManager in charge again) when it stops, including on Ctrl+C or a termination signal. It needs root and the `iw` package; `aiov2_ctl --install` (and so `--update`) installs `iw` for you. The sudoers rules from `--setup-sudoers` allow exactly `wlan1`, one listed channel and a fixed duration, with no wildcards. Frame contents are never read beyond the sender address, and nothing is saved.

