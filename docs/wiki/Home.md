# aiov2_ctl

**aiov2_ctl** is a system-level control and telemetry tool for the  **HackerGadgets uConsole AIO v2 Upgrade Kit**.

It provides:
- GPIO power control (GPS, LoRa, SDR, USB), now and at boot
- Battery-first power telemetry (hardware truth source)
- Live power monitoring and delta measurement
- A lightweight system tray GUI
- An installer you can read before it runs: in a terminal, as a picker, or as a window, with an uninstall that removes only what it added
- Updates on a stable or a beta channel
- The uConsole Ops app: a full-screen window for rails, vitals, radios and settings, and the Ops console for the terminal
- Passive Wi-Fi and Bluetooth listening, and read-mostly tools for a Flipper Zero or ESP32 Marauder board
- A read-only health check for the uConsole (`--doctor`)
- Turnkey setup for common radio and GNSS applications

This tool is designed to feel like a **first-class system utility**, not a script.

---

## Quick Start

As yourself, not as root:

```bash
curl -fsSLO https://raw.githubusercontent.com/AceMedia/aiov2_ctl/main/install.sh
sh install.sh
```

It shows its plan and asks before changing anything. See [Installation](Installation) for the picker, the installer window and choosing features.

Open a new shell and try:

```bash
aiov2_ctl --status
aiov2_ctl --gui
```

Bash tab-completion is installed automatically.

### GUI Autostart (Optional)

To start the system tray GUI automatically on login:

`aiov2_ctl --autostart`

To disable it later:

`aiov2_ctl --no-autostart`

---

## Hardware Context

This project targets the **uConsole AIO v2 Upgrade Kit**, providing software-level control and visibility over hardware.

Key characteristics:
- GPIO-controlled RF peripherals
- On-board battery and current sensing
- Battery current as the *only* reliable power truth
- CM4 / CM5 compatible
- Designed for off-grid, radio-heavy use

**Product page:**
https://hackergadgets.com/products/uconsole-upgrade-kit

---

## Important Notes (Read This)

Some behaviour users encounter is **hardware-related, not software bugs**.

If you experience instability with Wi-Fi + Bluetooth, SDR audio issues, or USB device resets, see:

- **Wi-Fi & Bluetooth power limitations**  
  → [Troubleshooting: Wi-Fi / Bluetooth power issue](Troubleshooting#wi-fi--bluetooth-stability-issues)

- **SDR++ audio missing on Debian Trixie**  
  → [Troubleshooting: No audio in SDR++](Troubleshooting#no-audio-in-sdr-debian-trixie--pipewire)

- **Only one SDR app can run at a time**  
  → [Troubleshooting: SDR conflicts](Troubleshooting#sdr-conflicts-one-app-at-a-time)

- **Compensation / repair options from HackerGadgets**  
  → [Troubleshooting: Compensation & replacement options](Troubleshooting#official-resolution-options)

These are documented plainly so users can make informed decisions.

---

## What aiov2_ctl Is (and Is Not)

**It is:**
- A control plane for the AIO v2 board
- A visibility layer for real power usage
- A practical workaround for hardware constraints

**It is not:**
- A driver replacement
- A fix for under-spec’d hardware
- A generic GPIO wrapper

---

## Wiki Structure

**Setting up**

- **[Installation](Installation)**: the quick install, choosing features, the picker and the installer window
- **[Uninstall](Uninstall)**: what is removed, what is left, removing one feature
- **[Updates and Channels](Updates-and-Channels)**: updating, stable and beta
- **[OS Compatibility & Requirements](OS-Compatibility-&-Requirements)**

**Using it**

- **[CLI Usage](CLI-Usage)**: commands, measurement mode, examples
- **[GUI](GUI)**: the tray, the status window, the installer window
- **[Ops App](Ops-App)**: the full-screen app, its tabs and keys
- **[Ops Console](Ops-Console)**: the same tools in a terminal
- **[Power & Telemetry](Power-&-Telemetry)**: how power is measured, noise floor, why battery current matters
- **[Applications](Applications)**: Meshtastic, SDR++, tar1090, PyGPSClient

**Radios and companions**

- **[GPS](GPS)**: the receiver, the GPS tab, tracks and waypoints
- **[LoRa Mesh](LoRa-Mesh)**: Meshtastic, the LoRa tab, messages and the radar
- **[SDR](SDR)**: the receiver, the SDR tab, Bias-T and aircraft
- **[Wi-Fi and Bluetooth](Wi-Fi-and-Bluetooth)**: the radios, passive scanning and listening, the wired network
- **[Companion Devices](Companion-Devices)**: Flipper Zero and ESP32 Marauder boards

**When something is wrong**

- **[Health Check](Health-Check)**: `aiov2_ctl --doctor`
- **[Troubleshooting](Troubleshooting)**: known hardware limits, SDR audio, Wi-Fi/Bluetooth power, install and update problems
- **[Reporting Problems](Reporting-Problems)**: logs, crash reports and opening an issue
