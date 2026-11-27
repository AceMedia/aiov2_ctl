This page documents common issues, expected behaviour, and known hardware limitations of the uConsole AIO v2 upgrade kit and `aiov2_ctl`.

The aim here is clarity and practicality — this is not guesswork, and it is not distro-specific folklore. Where limitations exist, they are documented plainly.

## Hardware Installation Warning

After installing the **AIO v2** or the **RJ45 Ethernet and USB 3.0 expansion board**, if the uConsole fails to boot or power on, **check the ribbon cable orientation immediately**.

Make sure the AIOv2 ribbon cable is installed as per this image.
<img style="width: 300px; height: auto;" alt="image" src="https://github.com/user-attachments/assets/d1924f03-a0d1-4a72-8cbb-6a60e4bd2ac3" />

⚠ **Never connect the charger if the ribbon cable is installed the wrong way round.**  
Doing so can permanently damage the uConsole mainboard.

If the system does not power up after installing the expansion board:

1. Disconnect all power immediately
2. Remove the expansion board
3. Re-check the ribbon cable orientation
4. Only reconnect power once the cable is confirmed correct

---

## Audio & SDR Behaviour

### No Audio in SDR++ (Debian Trixie / PipeWire)

On Debian Trixie, SDR++ does **not** automatically create an audio output sink.

This is expected behaviour and **not a fault in SDR++**.

**Fix:**

1. Open **SDR++**
2. Open the **left sidebar**
3. Go to **Module Manager**
4. Search for `audio`
5. Add **linux_pulseaudio_sink**
6. Open **Sinks** and select your audio output device

Once added, audio output will persist across restarts.

---

### SDR Conflicts (One App at a Time)

Only **one SDR application** can access the RTL-SDR at any given time.

This means:

- SDR++ and tar1090 **cannot run simultaneously**
- tar1090 uses `readsb`, which takes exclusive control of the SDR device

The AIO app setup handles this by:

- Starting `readsb` automatically when tar1090 is launched
- Stopping it again on exit so SDR++ can be used afterwards

If SDR++ reports the device as *busy* or *unavailable*, ensure tar1090 / readsb is not running.

---

## Power & Telemetry

### What `aiov2_ctl` Measures

`aiov2_ctl` treats **battery current** as the single source of truth.

- Battery rail current is used for all power calculations
- USB-reported values are ignored
- Internal rail estimates are ignored

This avoids misleading readings caused by USB negotiation, regulator losses, and transient load changes.

#### Noise Floor

- Power deltas below **0.05 W** are considered noise
- Differences under this threshold should be ignored

This is intentional and based on real-world measurement stability.

---

## Boot preference set but live rail OFF

If a feature is configured ON at boot but appears OFF live, check the boot apply path end-to-end.

### 1) Check configured boot preferences

```bash
aiov2_ctl --boot-rails-status
```

### 2) Check boot service state

```bash
systemctl status aiov2-rails-boot.service --no-pager
systemctl is-enabled aiov2-rails-boot.service
```

### 3) Check boot service logs for current boot

```bash
journalctl -u aiov2-rails-boot.service -b --no-pager
```

### 4) Check raw pin state and interpreted status

```bash
pinctrl get <pin>
AIOV2_CTL_DEBUG=1 aiov2_ctl --status
```

Interpretation:

- `hi/lo` should report source `(pinctrl)`
- `--` should report source `(boot_default)`

If needed, apply configured boot states immediately (without reboot):

```bash
sudo aiov2_ctl --apply-boot-rails
```

---

## Wi-Fi + Bluetooth Stability Issues

### Summary

Some uConsole AIO boards **cannot reliably power Wi-Fi and Bluetooth at the same time** without hardware modification.

This is **not**:

- a Linux driver problem  
- a kernel issue  
- a faulty MT7921AUN card  

It **is** a USB power delivery limitation on the AIO board.

---

### What’s Actually Happening

The MT7921AUN is a **USB combo device** providing:

- Wi-Fi
- Bluetooth

Both functions share the same USB power rail.

Key findings from testing and schematic review:

- Bluetooth initialisation causes short but real **current spikes**
- The AIO board limits USB current using a resistor (Rset)
- As shipped, Rset caps the USB rail at roughly **~1A**
- Wi-Fi alone remains stable
- Enabling Bluetooth causes:
  - brown-outs
  - USB controller resets
  - device disconnects

Reducing the Rset value (or adding appropriate buffering) resolves the issue.

---

### Why This Likely Occurred

Evidence strongly suggests:

- The power budget was validated against a **Wi-Fi-only device**
- A **Wi-Fi + Bluetooth combo card** was introduced very late
- The final configuration was not fully validated under dual-function load

Additional indicators:

- The antenna mount PCB includes extra connections beyond early documentation
- This strongly implies a late design pivot to support a combo module

---

### Clear Statement of the Issue

As shipped, some boards:

- Support Wi-Fi reliably
- Support Bluetooth unreliably or not at all
- Cannot power both functions simultaneously without modification

This is a **hardware power budget issue**, not a software one.

If Bluetooth stability is required, this limitation must be addressed.

---

### Official Resolution Options

HackerGadgets have acknowledged the issue and outlined the following options.

The guiding principle is that **users should not bear any additional cost**.

#### Option 1 — Self-Repair

- User performs the resistor modification themselves (0402 SMD)
- **Partial refund provided**
- No shipping required

#### Option 2 — Return for Repair

- Product is mailed back for repair
- **Shipping costs covered**
- Partial refund provided (slightly less than Option 1)

#### Option 3 — Full Refund

- Return the product as defective
- **Full refund**
- Shipping costs covered

For more info see: https://forum.clockworkpi.com/t/statement-regarding-aio-v2-internal-usb-c-power-supply-issue-and-compensation-plan/21131

---

## RTC & Backup Battery (SR69 / Button Cell)

The AIO v2 board includes a **hardware real-time clock (RTC)**.  
To retain correct time across power loss, the RTC requires a _**SR69 watch battery**_ fitted to the AIO v2 board.

Without this battery:

- The RTC will reset when the system loses all power
- Logged times, RTC sync operations, and time-based functions may behave unpredictably
- You may see the date/time revert to the epoch on reboot

### Installing the Battery

1. Locate the **RTC battery holder** on the AIO v2 board (small metal clip)
2. Insert the button cell with the correct polarity (flat side “+” up)
3. Power the system off and on to confirm the RTC keeps time

### Why it Matters

The RTC is used by:

- `aiov2_ctl --sync-rtc` (writes system time into hardware)
- Some GNSS or time-dependent applications that read the RTC
- Keeping correct time across power cycles without network time

If you do not install a backup battery, the RTC will not retain time when the unit is powered down.

---

## Practical Advice

- Do not assume Bluetooth will be stable without modification
- If you are comfortable with SMD work, the fix is small but precise
- If not, returning the board for repair or refund is entirely reasonable
- This limitation should be understood **before deployment**, especially for portable or field use

This page will be updated if hardware revisions or official fixes change the situation.

---

## Install and update problems

### The plan says a package is not in this system's package sources

- `meshtasticd` does not come from Debian, and `readsb` is in Debian 13 but not Debian 12. Everything else still installs. The feature is listed as "not installed", with where to get the package, and the install still ends "Install complete." `sudo aiov2_ctl --add-apps` sets up the Meshtastic package source; run the install again afterwards.
- A feature listed as "failed" is different: `apt` could not install a package it should have been able to. The line says which, and the install ends "not everything went in".
- On a system that has never fetched its package lists the plan says so instead, and the install fetches them first.

### The plan says the ops app and console cannot go in

They come with this tool's git checkout, and this install has none: `install.sh` had no `git` and downloaded only the one program file. Install `git` and run `install.sh` again. Everything else is installed either way.

### `~/ops-agentic` was left behind

Earlier versions kept the ops app in that folder. An install or update removes it only when it holds nothing of yours, and says why when it leaves it: changed files, stashed work, or commits that were never pushed. It is also left for one update if the app that asked for the update was running from it; start the app again and it goes at the next one. Nothing uses the folder any more, so you can delete it yourself.

### "Finish the update" in the tray or on `--status`

An update from an older version pulls the new program but installs it with the old version's steps. Finish it with `sudo aiov2_ctl --install`, or run `aiov2_ctl --update` a second time. The same message appears when a feature could not be installed during an update.

### The tray has a stock icon, or `--update` has nothing to pull

`install.sh` had no `git` and downloaded the one program file, so there is no checkout. Install `git` and run `install.sh` again.

### "Do not switch channel with sudo"

Run `aiov2_ctl --channel` and `aiov2_ctl --update` as yourself. They ask for root only for the step that needs it.

### A boot-rail command refuses to run

Anything run as root uses the installed copy only. Install first (`sudo python3 ./aiov2_ctl.py --install`), then run the command as `aiov2_ctl`.

### Rails come up differently after a reboot

The boot service sets every rail to off until you choose otherwise with `sudo aiov2_ctl --boot-rail <FEATURE> on`. Boot settings are read from the system config, `/usr/local/share/aiov2_ctl/config.json`, so set them with sudo. `aiov2_ctl --doctor` tells you when your user config and the system config disagree.

### A radio page has nothing on it

Each radio has its own page with the usual causes: [GPS](GPS), [LoRa Mesh](LoRa-Mesh), [SDR](SDR), [Wi-Fi and Bluetooth](Wi-Fi-and-Bluetooth). If radio programs cannot open the SDR dongle, try `sudo aiov2_ctl --fix-sdr`.

### Something else

Run `aiov2_ctl --doctor` ([Health Check](Health-Check)), then see [Reporting Problems](Reporting-Problems).
