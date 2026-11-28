# Installation

`aiov2_ctl` installs itself and, if you want them, the tray, the ops app, the ops console and the radio tools. Every route below uses the same installer and the same plan, so the terminal, the picker and the installer window all do the same thing.

The installer:

- shows its plan and asks before it changes anything;
- installs the packages each feature needs with `apt`;
- never switches a rail on, never starts a radio, and makes nothing start at boot except its own two housekeeping units and, only if you pick it, the tray at login;
- runs as root only what needs root. Anything in your home folder is done as you.

## Quick install

On a uConsole running Debian or Raspberry Pi OS, as yourself (not as root):

```bash
curl -fsSLO https://raw.githubusercontent.com/AceMedia/aiov2_ctl/main/install.sh
sh install.sh
```

`install.sh` keeps a checkout of this repository in `~/aiov2_ctl-ace`, checks the program is a whole Python file, says what it is about to run as root, and then runs the installer. If `git` is missing it offers to install it first.

Everything is in that one checkout: the controller, the tray, the ops app and the ops console. The app and the console run from it, as you, so there is nothing else to fetch.

To see the plan without changing anything (no root needed):

```bash
sh install.sh --dry-run
```

[![The plan from install.sh --dry-run](img/install/cli-05-install-plan.webp)](img/install/cli-05-install-plan.webp)

The installer prints every step and asks once:

[![The installer asking before it changes anything](img/install/cli-06-install-question.webp)](img/install/cli-06-install-question.webp)

When it finishes it lists what happened to each feature:

[![The result of an install](img/install/cli-07-install-result.webp)](img/install/cli-07-install-result.webp)

## Features

| Key | What it is | In the usual set |
|---|---|---|
| `core` | The controller and command line | always |
| `tray` | The system tray | yes |
| `app` | The ops app (the full-screen window) | yes |
| `console` | The ops console (terminal) | yes |
| `wiki` | This wiki as a tab in the ops app, read offline | yes |
| `wikipics` | The wiki's pictures, downloaded once (about 3 MB) | no |
| `ghost` | [Ghost hunt (for fun)](Ghost-Hunt): playful meters built from real readings; cannot detect anything | no |
| `gps` | GPS (`gpsd`) | yes |
| `mesh` | LoRa mesh (`meshtasticd`) | yes |
| `sdr` | Software radio (`rtl-sdr`, and a check that recovers `readsb`) | yes |
| `adsb` | Aircraft receiver (`readsb`) | no |
| `listening` | Passive Wi-Fi and Bluetooth listening without a password | yes |
| `flipper` | The rule that lets you open a Flipper Zero or ESP32 board on USB | yes |
| `autostart` | Start the tray when you log in | no |

See what is installed at any time:

```bash
aiov2_ctl --features
```

[![aiov2_ctl --features](img/install/cli-01-features.webp)](img/install/cli-01-features.webp)

## Choosing features

In a terminal:

```bash
sh install.sh --only core,tray          # just these, and what they need
sudo aiov2_ctl --install --all          # everything
sudo aiov2_ctl --install --pick         # a picker in the terminal
```

[![The terminal picker](img/install/cli-08-install-picker.webp)](img/install/cli-08-install-picker.webp)

In a window, from the start menu ("AIO v2 installer") or:

```bash
aiov2_ctl --installer
```

Tick what you want. "The usual set", "Everything" and "Only what is needed" set the ticks for you.

[![The installer window: choose what to install](img/install/gui-01-choose.webp)](img/install/gui-01-choose.webp)

"See the plan" shows exactly what will happen. Nothing has changed yet at this point.

[![The installer window: the plan](img/install/gui-09-plan-to-install.webp)](img/install/gui-09-plan-to-install.webp)

The window shows the installer's output while it works, and the result at the end.

[![The installer window: finished](img/install/gui-10-installed.webp)](img/install/gui-10-installed.webp)

## Repair

If a file the installer wrote has gone missing, put back what is recorded as installed:

```bash
sudo aiov2_ctl --repair
```

The installer window has the same thing as "Repair what is installed".

[![The installer window: the repair plan](img/install/gui-12-repair-plan.webp)](img/install/gui-12-repair-plan.webp)

## Things to know

- **Packages that are not in your package sources.** `meshtasticd` does not come from Debian, and `readsb` is in Debian 13 but not Debian 12. The installer installs everything else and lists that feature as "not installed", with where to get the package. That is not a failure: the install still ends "Install complete." `sudo aiov2_ctl --add-apps` sets up the Meshtastic package source; run the install again afterwards.
- **A fresh system image.** Before the system has fetched its package lists, the plan cannot know which packages exist. It says so, and the install fetches the lists first.
- **The ops app and console need the checkout.** If `install.sh` ran without `git`, it downloaded only the one program file. The plan then says the app and console cannot go in. Install `git` and run `install.sh` again.
- **An older install.** Earlier versions kept the ops app in a folder of its own, `~/ops-agentic`. An install or update removes that folder when it holds nothing of yours (no changed files, nothing unpushed, nothing stashed), and otherwise leaves it and says why.
- **The tray at login** is not in the usual set. Add it with `aiov2_ctl --autostart`.

See also: [Uninstall](Uninstall), [Updates and Channels](Updates-and-Channels), [OS Compatibility & Requirements](OS-Compatibility-&-Requirements).
