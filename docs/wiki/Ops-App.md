![AIO Ops: the uConsole AIO v2 ops app, with its four rails GPS, LoRa, SDR and USB](img/app/aio-ops-logo-full.webp)

The ops app is the full-screen window for the uConsole: the rails, vitals, radios and settings in one place. It is the `app` feature of the installer and opens from the start menu as "uConsole Ops", or from the tray's menu. Its code is `ops_gui/` in this repository.

The app sends nothing anywhere by itself. Logs and crash reports are off until you turn them on, stay on the uConsole, and are only ever shared by you.

Click a picture to see it full size.

## Which tabs you see

In order: Home, Overview, Everything, Wi-Fi, Bluetooth, Network, Marauder, GPS, LoRa, SDR, Flipper, Apps, Settings, Help, Wiki.

| Tab | When it is there |
|---|---|
| Overview, Everything, Wi-Fi, Settings, Help | always |
| Home | unless you turn it off in Settings > General |
| Bluetooth | while the internal Wi-Fi and Bluetooth card is there (it is on the USB rail) |
| Network | when the uConsole has a wired network port |
| GPS, LoRa, SDR | while that rail is on |
| Flipper | while a Flipper Zero is plugged in |
| Marauder | while an ESP32 Marauder board answers |
| Apps | when you turn it on in Settings > General |
| Wiki | when the Local wiki feature is installed |

Most of the pictures below were taken with every rail off, so the hardware tabs are not in their tab bar.

## Keys

| Key | What it does |
|---|---|
| `1` to `9` | open that tab |
| `Tab` / `Shift`+`Tab` | next or previous tab |
| Arrow keys | move the highlight, the same as the D-pad |
| `Enter` | press what is highlighted |
| `R` | refresh |
| `S` | scan Wi-Fi |
| `E` | Everything: open it and start a session |
| `X` | terminal drawer: activity and a shell |
| `T` | light or dark mode |
| `F` or `F11` | full screen on or off |
| `Esc` | leave full screen, close the drawer |
| `I` | Flipper live display (read only), when a Flipper is plugged in |
| `Q` | quit |

Rails:

| Key | Rail |
|---|---|
| `G` | GPS |
| `M` | LoRa |
| `D` | SDR |
| `U` | USB |
| `Shift`+`G`, `M`, `D` or `U` | measure that rail's power draw |

The uConsole's own controls:

| Control | What it does |
|---|---|
| D-pad | move the highlight: up to the row of tabs, left and right along it, down into the page, and between the controls on a page |
| A | press what is highlighted |
| B | back one level: out of the page to the tabs, then out of full screen |
| X | refresh |
| Y | full screen |
| Trackball buttons | right is back, middle is refresh |

A, B, X and Y work with the back switch in gamepad mode. In keyboard mode they send `j`, `k`, `u` and `i`.

Moving round with the D-pad:

[![Moving round the app with the D-pad](img/clips/dpad.webp)](img/clips/dpad.webp)

## The terminal drawer

`X` slides a panel up over the bottom of the app, so you do not have to leave full screen to see what the app did or to run a command.

- **ACTIVITY** is a short log of what the app just did: rails switched, scans, notices.
- **SHELL** is a small command line in your own account. It cannot run full-screen programs such as `top` or `nano`.
- **WINDOW** opens your system's own terminal beside the app.
- **CONSOLE** swaps to the [Ops Console](Ops-Console).

![](img/app/divider.webp)

## Tabs

### Home

A start screen: the clock, power and a ticker along the top; below, what is switched on and what it is doing, the machine's vitals, and the radios; then a row of soft keys on the function keys. Tap a rail to switch it. It is drawn to be cheap on the battery and stops updating while hidden.

[![Home](img/app/app-01-home.webp)](img/app/app-01-home.webp)

### Overview

Cards for battery and power, the rails and their switches, companions, GPS, LoRa and SDR status (each while its rail is on), the last Wi-Fi scan, the network address and power warnings. A card with nothing to show stays hidden. You choose the cards and their order in Settings > Dashboard.

On battery, each rail has a **Measure** button: it switches the rail, compares the battery's draw before and after, and remembers the result. It asks first. See [Power & Telemetry](Power-&-Telemetry).

[![Overview](img/app/app-02-overview.webp)](img/app/app-02-overview.webp)

With the rails on, the GPS, LoRa and SDR cards appear:

[![Overview with every rail on](img/app/rails-on-overview.webp)](img/app/rails-on-overview.webp)

### Everything

One session that records what every source that is already on can hear, side by side: Wi-Fi networks and device addresses, Bluetooth addresses, GPS satellites, the mesh, the SDR, and aircraft if `readsb` is already running.

- It never switches a rail on and never starts a service. A source that is off is greyed with the reason, joins if its rail comes on, and drops out if it goes off.
- It starts the Wi-Fi survey and the Bluetooth scan if they are not running. Stop only stops the scans the session started.
- It is passive. Nothing is sent.
- What is recorded is counts and readings, each with the time and, with a GPS fix, where the uConsole was.
- "What changed together" means two counts changed in the same minute, and nothing more.
- It cannot say where anything else is, which way it is, whether a device is a person, or whether two addresses are the same device, and it does not try.

After Stop, or for a saved session, there is a map of the uConsole's own track with a mark for each network or address from a log saved during the session. A mark is where the uConsole was when it heard that thing strongest, not where the thing is. There are no map tiles and nothing is fetched. The track and marks can be saved as GPX or GeoJSON beside the session, privately.

[![Everything](img/app/app-03-everything.webp)](img/app/app-03-everything.webp)

### Wi-Fi

Passive listening only. Five pages:

- **Networks.** Press Scan (or `S`) to list networks, strongest first, with what each radio heard of them. Filter by name or address.
- **Channels.** Networks per channel, with plain advice: the quietest of 1, 6 and 11, and empty 5 GHz channels.
- **Radios and saved.** The Wi-Fi radios, and saved scans (private, in `~/aiov2-captures`).
- **Survey.** What the internal radio hears in monitor mode: access points, devices, probing devices, deauthentication bursts (detection only) and how busy each channel is. It can save a capture with data payloads cut off, and a wardriving log exported as a WiGLE CSV. It never transmits and always puts the radio back.
- **Sense.** Notices movement near the uConsole from how Wi-Fi signals wobble. It is a coarse "is something moving" sensor, not a camera: it cannot see who, what or where.

Survey and Sense need the USB rail on and the `listening` feature. See [Wi-Fi and Bluetooth](Wi-Fi-and-Bluetooth).

[![Wi-Fi](img/app/app-04-wi-fi.webp)](img/app/app-04-wi-fi.webp)

### Bluetooth

The Bluetooth Low Energy devices advertising around you, heard passively on the internal card. It never sends a scan request, connects, pairs or advertises. A scan stops by itself.

- Each row has a name if the device sends one, who made it, what sort of address it uses and how strong its signal is here.
- It cannot tell you where a device is, how far away it is or who it belongs to.
- Phones, watches and earbuds change their address every few minutes, so one device can appear as several rows.
- It remembers fixed addresses heard on other days, and you can mark addresses as your own. Addresses that change are never stored.
- **Log with position** keeps a log with where the uConsole was. It needs the GPS rail on and a fix.

See [Wi-Fi and Bluetooth](Wi-Fi-and-Bluetooth).

[![Bluetooth, before a scan](img/app/tab-bluetooth.webp)](img/app/tab-bluetooth.webp)

### Network

The wired link (cable, speed, address) and the hosts the system already knows about.

- **Listen 30s** is passive: it hears what hosts announce and sends nothing.
- **Sweep my subnet** and **Check ports** send traffic, so they ask first and only work on your own private network. Only use them on a network you own or have permission to look at.

[![Network, with no cable plugged in](img/app/tab-network.webp)](img/app/tab-network.webp)

The MAC addresses are covered in this picture.

### GPS, LoRa and SDR

Each has its own page, with pictures: [GPS](GPS), [LoRa Mesh](LoRa-Mesh), [SDR](SDR).

### Flipper and Marauder

The Flipper tab has the status, the live screen, the SD card (Files), the apps it can launch (Apps, Get apps) and GPIO. The Marauder tab runs the passive Wi-Fi scans of an ESP32 Marauder board; no attack, spoofing or portal commands exist in it. See [Companion Devices](Companion-Devices).

### Settings

Nine pages, described below.

[![Settings](img/app/app-06-settings.webp)](img/app/app-06-settings.webp)

### Help

The tabs with their number keys, the keys, each rail's key with whether that rail is on now, the uConsole's buttons, and the companions: Flipper, gamepad, USB rail and the internal Wi-Fi and Bluetooth card.

[![Help](img/app/app-07-help.webp)](img/app/app-07-help.webp)

### Wiki

This wiki, inside the app, read from the copy that came with the install. It works with no network. The list of
pages is on the left and the page on the right; a link to another site opens in your web browser.

The pictures are a separate feature, "Wiki pictures": about 2 MB, downloaded once. Without them each picture
is replaced by a line saying what it shows. Add either in Settings > Features or with
`sudo aiov2_ctl --install --only wikipics`.

![](img/app/divider.webp)

### Apps

Off until you switch it on: **Settings > General > Show the Apps tab**. It shows the programs installed on the uConsole as a grid of tiles, the same ones the desktop's menu shows, whether they came from a package, a Flatpak, a Snap or an entry you added yourself. They are sorted into the menu's sections, one sub-tab each, with **Games** first, then Internet, Office, Graphics, Sound & Video, Programming, Education, Accessories, System, Settings and Other. A section with nothing in it is left out.

Choose a tile and press **Open**, `Enter` or **A** to start it; a program that draws in a terminal is opened in one. The D-pad and the arrow keys move from tile to tile in all four directions, up to the sub-tabs and down to the buttons. **Look again**, `R`, or **X** on the gamepad rereads the list after you install or remove something.

The app only starts the program. It downloads nothing and sends nothing.

[![Apps](img/app/app-08-apps.webp)](img/app/app-08-apps.webp)

[![Moving through the Apps grid with the D-pad](img/clips/apps.webp)](img/clips/apps.webp)

## Settings pages

### General

The display (light, dark or following the system; high contrast; the background as a dotted gradient, one flat colour or solid black; a font from the uConsole in place of the app's own), the feature colour used for borders and highlights on every screen, whether to show the Home tab, whether to show the Apps tab, whether to open in full screen at login, how often the Overview checks the hardware, and whether the tray icon starts when you log in.

[![Settings, General](img/app/settings-01-general.webp)](img/app/settings-01-general.webp)

High contrast, then the plain and black backgrounds:

[![High contrast and the plain and black backgrounds](img/clips/looks.webp)](img/clips/looks.webp)

### Notifications

What to tell you about, as pop-up notices inside the app and, if you want, desktop notifications too. **Send a test notice** shows what one looks like. Mesh notices need the LoRa rail on.

[![Settings, Notifications](img/app/settings-02-notifications.webp)](img/app/settings-02-notifications.webp)

### Dashboard

Which cards show on the Overview and in what order, which rails appear, and what shows along the bottom of the window. **Reset to the default layout** puts it back.

[![Settings, Dashboard](img/app/settings-03-dashboard.webp)](img/app/settings-03-dashboard.webp)

### Power

Which rails come on at boot, and the measured power of each rail, with **Clear measurements**. Rails stay off at boot unless you choose otherwise with `sudo aiov2_ctl --boot-rail GPS on` (or `off`).

[![Settings, Power](img/app/settings-04-power.webp)](img/app/settings-04-power.webp)

### LoRa

How `meshtasticd` starts: with the LoRa rail by default, or also at boot with `aiov2_ctl --mesh-on-boot`.

[![Settings, LoRa](img/app/settings-05-lora.webp)](img/app/settings-05-lora.webp)

### Wi-Fi

Saved scans. They are kept private to your account.

[![Settings, Wi-Fi](img/app/settings-06-wi-fi.webp)](img/app/settings-06-wi-fi.webp)

### Flipper

Everything Flipper related is optional and only appears when one is plugged in.

[![Settings, Flipper](img/app/settings-07-flipper.webp)](img/app/settings-07-flipper.webp)

### Features

What the installer has set up, with a button to add or remove each feature. Adding or removing one runs the same installer as the command line and asks first. Removing never uninstalls a package, switches a rail or deletes your saved settings. **Open the installer window** does several at once and repairs what is missing. The update channel is here too: **Switch channel** moves between stable and beta, and switching to beta turns on crash saving. See [Installation](Installation) and [Updates and Channels](Updates-and-Channels).

[![Settings, Features](img/app/settings-08-features.webp)](img/app/settings-08-features.webp)

### Debug

- **Keep a local log of what the app does.** With it on, the app also keeps a field log: the GPS reading every few seconds, and the rails, battery, temperature and what each scan has gathered every half minute. The field log holds positions and is never put into a report.
- **Save a local crash report if the app crashes.**
- **Report a problem** prepares a report you can read first, with your user name, host name, IP and MAC addresses removed, then opens a blank GitHub issue for you to paste it into.
- **Find this crash on GitHub** searches for reports with the same crash code.
- **Delete local log and crash reports.**

The log and field log are in `~/.local/state/ops_gui`.

See [Reporting Problems](Reporting-Problems).

[![Settings, Debug](img/app/settings-09-debug.webp)](img/app/settings-09-debug.webp)
