# Health Check

```bash
aiov2_ctl --doctor
```

A read-only check of the uConsole. It changes nothing. Each line is `OK`, `INFO` or `WARN`.

[![aiov2_ctl --doctor](img/install/cli-11-doctor.webp)](img/install/cli-11-doctor.webp)

## What it looks at

| Area | What it checks |
|---|---|
| RTC | `dtparam=rtc=off` is paired with the AIO board's `i2c-rtc,pcf85063a` overlay, and the clock registered. A power-on-reset message at boot usually means the CR1220 battery is flat, loose or missing. |
| Kernel | A `clockworkpi-kernel` or `akrex-kernel` package is installed, and a full upgrade would not remove a kernel or firmware package. |
| EEPROM | The bootloader date. A CM5 Lite wants one newer than 2025-01-06. It reports only and never writes to the EEPROM. |
| config.txt | Known bad settings. For example `dtparam=spi=on` disables the backlight on the CM5; use `dtoverlay=spi1-1cs`. |
| Battery and power | Whether the gauge percentage and the cell voltage agree, and any undervoltage since boot. |
| Boot rails | Whether your user config and the system config say the same thing. |
| Storage | A read-only root filesystem, or filesystem and card errors in this boot's log. |
| Audio | A note about the harmless `alsa_restore_std` warning from alsa-utils 1.2.14-1. |
| Tray | Whether the login entry is current and the tray is running. |
| Mesh | Whether the LoRa rail and `meshtasticd` are on. |

Never run `rpi-update` on a uConsole.

The checks come from ClockworkPi forum posts, the Raspberry Pi documentation and the Debian bug tracker. Treat them as advice.
