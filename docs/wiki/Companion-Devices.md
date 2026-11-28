# Companion Devices

Tools for a Flipper Zero and for ESP32 boards running Marauder, plugged into the uConsole. They only apply when the device is plugged in; with nothing there, no port is opened.

## Finding them

```bash
aiov2_ctl --companions
aiov2_ctl --companions --json
```

Looks on the USB bus for a Flipper Zero (`0483:5740`) and ESP32-style boards (Espressif native USB, and generic CP210x/CH340 chips, which are reported as "possible"). It shows the serial port, and warns if the USB rail is off or your user cannot open the port. The same line appears in `--status` and in the tray menu. Detection opens nothing and writes nothing.

The `flipper` feature of the installer adds `/etc/udev/rules.d/99-aiov2-companions.rules`. It gives the logged-in user access to the port (no world-writable devices), tells ModemManager to leave it alone, and adds stable names: `/dev/aiov2-flipper` and `/dev/aiov2-esp32`. Replug the device after installing.

The ops console and app look for a LoRa radio on `ttyACM0`, `ttyUSB0`, `ttyACM1` and `ttyUSB1`, skip a Flipper's or ESP32 board's port, and open a port only when its USB identity is a chip Meshtastic boards use (see [LoRa Mesh](LoRa-Mesh)). If LoRa will not connect, check `--companions` and use the `/dev/aiov2-*` names.

## Flipper Zero

```bash
aiov2_ctl --flipper info
aiov2_ctl --flipper ls [/ext]
aiov2_ctl --flipper apps
aiov2_ctl --flipper open <APP>
aiov2_ctl --flipper clock [set --yes]
```

- `info` shows hardware, firmware and battery; `ls` lists files (the SD card by default); `apps` lists what can be launched; `open` starts one.
- `clock` prints the Flipper's clock beside this computer's local time. `clock set --yes` sets the Flipper's clock to this computer's local time and reads it again. Without `--yes` it changes nothing.
- It talks to the Flipper's own serial command line with the Python standard library, so there is nothing extra to install.
- Add `--port /dev/ttyACMx` to pick a port.
- Close qFlipper first: the port is opened exclusively.

### Files

```bash
aiov2_ctl --flipper put ./payload.sub /ext/subghz/payload.sub [--force]
aiov2_ctl --flipper get /ext/subghz/payload.sub ./copy.sub
aiov2_ctl --flipper get /ext/subghz/payload.sub    # saves under ~/aiov2-captures/flipper/
aiov2_ctl --flipper mkdir /ext/mine
```

- Transfers only touch the SD card (`/ext`), never the Flipper's internal storage.
- `put` refuses to overwrite without `--force` and checks the MD5 afterwards.
- `get` never overwrites a local file and saves with mode 600.
- Files are limited to 64 MiB.

In the Ops app, a Flipper tab appears while a Flipper is plugged in, and the `I` key shows the Flipper's display (read only). See [Ops App](Ops-App) for what the tab shows.

## ESP32 Marauder (passive only)

```bash
aiov2_ctl --marauder info
aiov2_ctl --marauder scan [all|beacon] [--seconds N]
aiov2_ctl --marauder list [aps|stations]
aiov2_ctl --marauder channel [N]
aiov2_ctl --marauder stop
```

Drives the Marauder firmware's serial command line (115200 baud) on an ESP32 board, plugged in directly or through the Flipper's USB-UART Bridge app.

- Only a short list of passive commands is built in. There is no way to send attack, spoofing or portal commands from here.
- `scan` streams what the board hears, saves it to `~/aiov2-captures` (folder 700, files 600), and always sends `stopscan` afterwards, including after Ctrl+C.
- Scans need time: 10 seconds may hear nothing; the default of 30 seconds finds far more.
- While the Flipper's bridge app is running, the Flipper's own command line is not available, so `--flipper` commands do not work until you leave the app.

The logs contain the names and MAC addresses of nearby networks, so treat them as private. This is for looking at your own networks and equipment, and the law where you live applies.

## Power

A Flipper charging from a uConsole port draws from the battery. The USB rail powers the ports: `aiov2_ctl USB on`.
