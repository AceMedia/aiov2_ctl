# Reporting Problems

The ops app never sends anything by itself. Logging and crash reports are off until you turn them on, they stay on your uConsole, and sharing a report is always a step you take yourself.

## Turning it on

Settings, Debug:

- "Keep a local log of what the app does"
- "Save a local crash report if the app crashes" (switching to the beta channel turns this on)

[![Settings, Debug in the ops app](img/app/settings-09-debug.webp)](img/app/settings-09-debug.webp)

## After a crash

The next time the app starts it shows a notice with two buttons:

- **Report** opens Settings, Debug and prepares the report.
- **Dismiss** hides the notice until there is another crash.

## The report

"Prepare report" builds the text on screen so you can read it first. It holds versions, the status and `--doctor` output and the recent log, with your username, host name, IP and MAC addresses removed. No Wi-Fi scans or captures are included.

## Sending it

Each crash has a short code, for example `Crash 390557c1: KeyError in wifi_ui.refresh`. The same crash has the same code on every uConsole and across versions.

1. Press **Find this crash on GitHub**. It searches the issues for the code.
2. If an issue is already there, add your report to it.
3. If not, press **Open a GitHub issue**. The report is copied for you; paste it in and submit it.

## Deleting it

Settings, Debug, "Delete local log and crash reports".
