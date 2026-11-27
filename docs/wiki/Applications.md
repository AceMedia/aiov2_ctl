# Applications

This page covers the primary applications installed via `sudo aiov2_ctl --add-apps`, with practical tips and regional notes. `sudo aiov2_ctl --remove-apps` removes them again.

These are separate programs from the Ops app, which has its own pages for the same radios: [GPS](GPS), [LoRa Mesh](LoRa-Mesh) and [SDR](SDR).

---

## Meshtastic

```bash
meshtastic-ui
```

The package is called `meshtastic-mui`; the command and the start-menu entry open the same program.

Meshtastic provides an off-grid, low-power LoRa mesh network for messaging and telemetry.

### Power
Ensure the LoRa module is powered on:
```bash
aiov2_ctl LORA on
```

---

### Meshtasticd boot control
`meshtasticd` is the backend service. By default, autostart is disabled so LoRa power controls when the service runs.

Check status:

`aiov2_ctl --mesh-on-boot status`

Force autostart on boot:

`aiov2_ctl --mesh-on-boot`

Disable autostart (default):

`aiov2_ctl --mesh-on-boot off` or `aiov2_ctl --mesh-off-boot`

After `--add-apps` or `--update`, the tool will show the current boot status and how to override it.

---

### Regional configuration
Meshtastic **must** be configured for your regulatory region.

- **UK / EU**
  - Set Region: **EU_868**
  - Observe duty cycle limits
- **US**
  - Set Region: **US**
  - Set **Frequency Slot = 20**
- **Australia / NZ**
  - Set Region: **ANZ**
- **Other regions**
  - Always select the closest regulatory domain
  - Do not transmit outside your local band plan

### Tips
- Set your **call sign** and **country** in settings.
- Initial pairing is easiest via the GUI.
- Map packs go in:
  ```
  /home/USER/.portduino/default/maps
  ```
- If the device does not appear, power-cycle LoRa and restart Meshtastic.

---

## SDR++ (Brown)

```bash
sdrpp
```

SDR++ Brown is a lightweight, bloat-free SDR application bundled for the uConsole AIO platform.

### Power
```bash
aiov2_ctl SDR on
```

### Basic use
- Select **RTL-SDR** from the *Sources* dropdown (top-left)
- Click ▶ **Play** to start receiving

### Audio on Debian Trixie (PipeWire)
If no audio is heard on first launch:

1. Open the left sidebar
2. Scroll to **Module Manager**
3. Search for `audio`
4. Add `linux_pulseaudio_sink`
5. Open **Sinks** and select your audio output device

### Notes
- Only **one SDR application can access the device at a time**
- If SDR++ fails to start, ensure `readsb` or another SDR app is not running

---

## tar1090

tar1090 provides a web interface for ADS-B aircraft tracking.

It runs on top of **readsb**, which takes exclusive control of the SDR.

### Notes
- Only one SDR application can run at once — this is expected
- This setup starts `readsb` when tar1090 launches and stops it on exit
- If aircraft do not appear:
  - Check antenna placement
  - Allow time for initial reception
  - Ensure the SDR rail is powered

---

## PyGPSClient

```bash
pygpsclient
```

PyGPSClient is a graphical GNSS / GPS diagnostics and configuration tool.

### Power
```bash
aiov2_ctl GPS on
```

### Tips
- Initial GPS lock may take several minutes depending on antenna quality
- Indoor use may significantly delay or prevent a fix
- It is set up to open `/dev/serial0` (the GPS UART) at 9600 baud
- Click the **USB/UART** icon to connect
- To keep the GPS powered at boot: `sudo aiov2_ctl --boot-rail GPS on`

---

## General Notes

- A reboot after installing applications is recommended
- RF subsystems may take time to initialise after power-on
- If something behaves unexpectedly:
  - Check power state with `aiov2_ctl --status`
  - Power-cycle the affected module
  - Ensure no conflicting applications are running

Power doesn’t lie.  
Battery current is the truth source.