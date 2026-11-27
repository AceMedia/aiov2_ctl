# SDR

The AIO board has an RTL-SDR receiver on its own rail. It receives only: nothing here can transmit.

The `sdr` feature of the installer installs the RTL-SDR tools and a recovery that brings back a tuner that has stopped answering. The `adsb` feature (off by default) installs `readsb`, the aircraft receiver.

## Switching it on

```bash
aiov2_ctl SDR on
aiov2_ctl SDR off
```

Or tap the SDR rail in the tray or the Ops app, or press `D` in the app.

## One program at a time

The dongle has a single owner. The Ops app's SDR tab, the Ops console, SDR++, `readsb` and tar1090 cannot use it at the same moment. If a program says the device is busy, close the other one.

## In the Ops app

The SDR tab is there while the SDR rail is on. The radio starts when you open the tab and is given back as soon as you leave it.

The top of the page is a spectrum with a waterfall under it and the frequency drawn over it. Click the spectrum to tune to that point. Below are rows of large keys:

- **Tuning.** `<` and `>` step one channel. `<<` and `>>` seek: move to the next signal in this band and stop there.
- **SCAN** keeps seeking through the band, waiting a few seconds on each signal. Press it again, or tune by hand, to stop.
- **Frequency box.** Type a frequency in MHz (`98.8`, `145.5`, `27.185`) or a CB channel (`cb 19`) and press Enter.
- **Bands and modes.** Ready-made bands set the mode and filter for you, among them broadcast FM, airband, 2 m and 70 cm amateur, marine VHF, PMR446, CB and NOAA weather. There are also lists of CB channels and FM stations. Modes are WFM, FM, NFM, AM, USB, LSB and CW.
- **Listen, volume and squelch.** Sound is off until you turn **Listen** on. Squelch mutes the speaker until a signal is stronger than the level you set.
- **Upconverter.** For short wave with an upconverter such as the Nooelec Ham It Up (+125 MHz), or a custom offset. With it set, you tune the real frequency, for example 7.1 for 40 m.
- **Bias-T.** See below.

A signal meter shows how far the tuned signal stands over the noise, in dB.

[![The SDR tab](img/app/tab-sdr.webp)](img/app/tab-sdr.webp)

## Bias-T

Bias-T puts a few volts on the antenna lead to power an amplified antenna, an LNA or an upconverter. It can damage an antenna that is a DC short.

- It is never switched on for you.
- In the Ops app it is switched off when you leave the SDR tab.
- Keep it off unless what is on the antenna lead needs it.

For the aircraft receiver there is a separate setting, because Debian's `readsb` does not switch the bias tee itself:

```bash
sudo aiov2_ctl --sdr-biastee on
sudo aiov2_ctl --sdr-biastee off
aiov2_ctl --sdr-biastee status
```

This powers the antenna input each time `readsb` starts. It stays on until the SDR loses power, so turn it off before connecting a passive antenna.

## Aircraft (ADS-B)

`readsb` decodes aircraft positions, and tar1090 shows them on a web page. `sudo aiov2_ctl --add-apps` sets up tar1090 so that `readsb` starts when tar1090 is opened and stops when it is closed. See [Applications](Applications).

- The tray's **App Services** menu shows whether `readsb` is running and can start, stop or restart it.
- In the Ops app, the Everything tab shows how many aircraft `readsb` hears, as a count and nothing else, and only when `readsb` is already running. It never starts it.
- `readsb` is in Debian 13 but not Debian 12. On a system without it the installer says so and installs the rest.

## If the dongle is not found

```bash
sudo aiov2_ctl --fix-sdr
```

The kernel's DVB-T television driver can claim the dongle before any radio program gets it. This frees it. Also check that the SDR rail is on and that no other program has the dongle.

## In the Ops console

The console has an SDR panel with keys for tuning, bands, presets, scan, squelch, volume, Bias-T and the upconverter. See [Ops Console](Ops-Console).

## SDR++

`sudo aiov2_ctl --add-apps` installs SDR++ Brown, a full SDR program. See [Applications](Applications), and [Troubleshooting](Troubleshooting) if it has no sound on Debian 13.
