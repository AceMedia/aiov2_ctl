This page pulls together practical OS-level notes, quirks, and recommendations for the uConsole, specifically in support of the AIO v2 hardware and tooling.

---

## ClockworkPi-Trixie-6.12.y (Rex’s Image)

This is currently our **recommended Debian-based image** for uConsole.

For **downloads, updates, changelogs, and support**, always refer to Rex’s original forum thread:

➡ https://forum.clockworkpi.com/t/trixie-6-12-y-for-the-uconsole-and-devterm/19457

That thread is the authoritative source for this image.

### Raspberry Pi Imager Warning

⚠ **Do not apply custom settings in Raspberry Pi Imager** when flashing this image.

Doing so can cause the system to **fail to boot**, particularly on **CM5 Lite** modules. **Always flash the image _as-is_.**

---

## CM5 Lite SD Boot Issues (Important)

If you are using a **CM5 Lite** and experience SD card boot failures or instability, this is **almost always EEPROM-related**.

### Requirements

- CM5 firmware **newer than 2025-01-06**
- Raspberry Pi Imager **must NOT** be used with custom settings  
  Applying custom settings can make the image unbootable

### Check firmware version

```bash
vcgencmd version
```

### Update EEPROM

```bash
sudo rpi-eeprom-update
```

### Edit EEPROM configuration

```bash
sudo rpi-eeprom-config -e
```

**Back up the existing configuration**, then replace it with:

```
[all]
BOOT_UART=1

# Switch off PMIC outputs on HALT
POWER_OFF_ON_HALT=1

# Default BOOT_ORDER for provisioning
# SD -> NVMe -> USB -> Network
BOOT_ORDER=0xf461

# Try boot on SDCard repeatedly
SD_BOOT_MAX_RETRIES=2

# Slow down SDCard SDR Mode on bootloader
SD_QUIRKS=1
```

Save, exit, and reboot.

---

## Display Bring-Up & Black Screen Issues (AIO v2 / CM5)

A black screen on boot is one of the most common failure modes on the uConsole with AIO v2 hardware. In most cases the system **is actually running** (SSH works, services start), but the display pipeline never initialises. This is almost always a missing or incomplete set of overlays.

### Required display configuration (Pi 5 / CM5)

The following items must all be present and working together:

- **Correct uConsole panel overlay**
- **Pi 5 KMS graphics driver**
- **Sufficient CMA memory**
- **SPI initialisation for the display controller**

If any one of these is missing, the LCD may never light up.

### Critical lines

Edit `/boot/firmware/config.txt` over SSH or via the SD card in another computer:

```ini
dtoverlay=clockworkpi-uconsole-cm5
dtoverlay=vc4-kms-v3d-pi5,cma-384
max_framebuffers=2
ignore_lcd=0
```

What these do:

- `clockworkpi-uconsole-cm5`  
  Enables the correct LCD panel, GPIOs, and backlight routing.

- `vc4-kms-v3d-pi5,cma-384`  
  Loads the **correct Pi 5 graphics stack** and allocates enough contiguous memory for the display to initialise reliably.  
  Without sufficient CMA, the display can silently fail during KMS handoff.

- `max_framebuffers=2`  
  Prevents framebuffer starvation during early boot and mode switching.

- `ignore_lcd=0`  
  Explicitly enables the internal LCD path.

### SPI dependency (easy to miss, but critical)

On AIO v2 systems, **SPI initialisation is required for the display to come up**.

If SPI0 is not enabled, the panel may remain black even though everything else is correct.

```ini
dtoverlay=spi0-0cs
```

This line has repeatedly been the “last missing piece” when display issues persist.

---

## PCIe Configuration, Stability, and Power Draw

The CM5 exposes a **PCIe Gen3-capable x1 link**, but in practice this can be unstable depending on:

- M.2 adapter quality
- Trace length
- Power delivery
- NVMe controller behaviour

For this reason, we deliberately cap the link to Gen2:

```ini
dtparam=pciex1
dtparam=pciex1_gen=2
```

### Why force Gen2?

- **Stability**: Gen2 is far more tolerant of real-world signal conditions.
- **Power draw**:  
  PCIe PHY power scales aggressively with link speed.  
  For many NVMe drives:
  - Gen4 can draw ~8–10 W under load
  - Gen3 ~6–7 W
  - **Gen2 ~4–5 W**

On uConsole-class hardware, forcing Gen2 dramatically reduces:
- thermal load
- brown-outs on cheaper M.2 hats
- unexplained NVMe resets

Latency and random I/O performance remain vastly better than SD or USB storage, even with bandwidth capped.

---

## What the installer needs

- **Debian 12 (bookworm) or Debian 13 (trixie)**, or a system built on one of them, such as Raspberry Pi OS or Kali. The installer has been run from nothing on a clean Debian 13 system.
- `python3`, `sudo` and `curl`. The installer offers to install `git` if it is missing, and installs everything else it needs with `apt`.
- A desktop, for the tray, the installer window and the Ops app. The command line and the Ops console work without one.
- `meshtasticd` does not come from plain Debian. `sudo aiov2_ctl --add-apps` sets up the Meshtastic package source for Debian 12 or 13, whichever you run.
- `readsb` is in Debian 13. Debian 12 does not have it.

`aiov2_ctl --doctor` checks the kernel, the EEPROM date and the rest: see [Health Check](Health-Check).

---

## Example `/boot/firmware/config.txt` (Known-Good Baseline)

The following is a **working reference configuration** for uConsole + **AIO v2 + CM5**, covering:

- Internal LCD bring-up
- SPI requirements for the display
- Stable graphics initialisation
- PCIe Gen2 power/stability tuning

```ini
[all]
# Core system
disable_overscan=1
dtparam=audio=on
max_framebuffers=2
ignore_lcd=0

# USB / peripherals
dtoverlay=dwc2,dr_mode=host
dtparam=ant2
dtoverlay=audremap,pins_12_13

# SPI (required for AIO v2 display)
dtoverlay=spi0-0cs
dtoverlay=spi1-1cs

[pi5]
# uConsole CM5 carrier + panel
dtoverlay=clockworkpi-uconsole-cm5

# Correct Pi 5 KMS graphics + sufficient CMA
dtoverlay=vc4-kms-v3d-pi5,cma-384

# UART
dtparam=uart0

# PCIe (stability + lower power draw)
dtparam=pciex1
dtparam=pciex1_gen=2

# Kernel (match image defaults)
kernel=kernel8-16k.img
```
