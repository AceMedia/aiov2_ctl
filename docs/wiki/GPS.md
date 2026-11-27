# GPS

The AIO board's GPS receiver is on its own rail. The `gps` feature of the installer installs `gpsd`, which reads the receiver for the Ops app and the Ops console.

## Switching it on

```bash
aiov2_ctl GPS on
aiov2_ctl GPS off
```

Or tap the GPS rail in the tray or the Ops app, or press `G` in the app. The rail is off at boot unless you choose otherwise:

```bash
sudo aiov2_ctl --boot-rail GPS on
```

A first fix can take several minutes, and may not come at all indoors.

## In the Ops app

The GPS tab is there while the GPS rail is on. It has two pages.

### Live

- Fix, position, speed and accuracy.
- A sky plot: each satellite by where it is in the sky, overhead in the middle and the horizon on the rim.
- A signal bar per satellite. Green bars are the ones used in the fix.
- **Copy coordinates** puts the position on the clipboard.
- Save a waypoint: one line is added to `~/aiov2-captures/waypoints.csv`.

The app only talks to `gpsd` while this tab is showing.

[![GPS, Live, indoors before a fix](img/app/tab-gps-live.webp)](img/app/tab-gps-live.webp)

The picture was taken indoors, a minute after the rail came on, so there is no fix yet.

### Track

- The position in four forms: decimal degrees, degrees and minutes, degrees minutes and seconds, and a Maidenhead locator.
- A track recorder. It is off until you turn its switch on. While on, it writes where you go to a GPX file in `~/aiov2-captures/gps`. Stopping without having moved writes no file.
- Trip figures and a north-up trace of the track with a scale bar. There are no map tiles and nothing is fetched.
- The waypoints saved on the Live page, newest first.

[![GPS, Track](img/app/tab-gps-track.webp)](img/app/tab-gps-track.webp)

Track and waypoint files are private to your account (mode 600) and never leave the uConsole.

## Where else the position is used

- **Wi-Fi Survey and Bluetooth** can keep a log with a position for each thing heard. The position is where the uConsole was, not where the network or device is. Both need the GPS rail on and a fix, and neither switches the rail for you.
- **Everything** stamps its readings with the position when there is a fix, and draws the uConsole's own track afterwards. See [Ops App](Ops-App).
- **The field log** (Settings > Debug, off until switched on) records the GPS reading every few seconds. It holds positions, so it is never put into a problem report.

## In the Ops console

The console shows a GPS panel when the rail is on. See [Ops Console](Ops-Console).

## PyGPSClient

`sudo aiov2_ctl --add-apps` installs PyGPSClient, a graphical tool for looking at the receiver in detail. See [Applications](Applications).

## If there is no position

- Check the rail: `aiov2_ctl --status`.
- Check the service: `systemctl status gpsd`.
- Go outdoors or near a window and wait a few minutes.
- Only one program should read the receiver's serial port directly. The app and console go through `gpsd`; PyGPSClient opens `/dev/serial0` itself.
