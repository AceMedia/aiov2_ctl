aiov2_ctl relies on **actual hardware measurements** from your uConsole with the HackerGadgets AIO v2 (All-In-One) expansion board.  
This expansion board integrates radio peripherals, GPS, a real-time clock, USB hub, and more, all of which can be individually powered on or off using GPIOs controlled by aiov2_ctl — making accurate power measurement useful and necessary.

---

## Battery-First Telemetry

The AIO board and uConsole platform operate from the system battery when not connected to USB power. Because other rails and power sources can be influenced by USB hub logic, regulator quirks, or peripheral draw, **battery current is always treated as the source of truth** for power measurements in aiov2_ctl.

### Why the Battery Rail Matters

- The AIO v2 board isn’t just a breakout — it uses the same battery rail that powers the uConsole and peripherals like SDR and LoRa.
- USB or DC input may be connected or disconnected at runtime; relying on them for net power can be misleading.
- The battery’s current measurement reflects *actual system load*, including all enabled features.

---

## Understanding Measurements

When aiov2_ctl reports power, you’ll see:

### Voltage (V)
The instantaneous battery voltage.

### Current (A)
Signed current from the battery:
- **Positive** → charging
- **Negative** → discharging

### Power (W)
Battery power = voltage × current.

---

## Noise & Accuracy

Power readings below **~0.05 W** are considered noise.  
This is due to small fluctuations in current measurements that may be caused by ADC noise, switching regulators, or sensor quantization. Such small deltas don’t reliably indicate real peripheral power changes.

> In practice, turning a hardware feature on or off often results in changes well above this noise floor.

---

## GPIO-Controlled Power Domains

The AIO v2 board adds fine-grained control over multiple devices:

- **GPS** – GPIO 27
- **LoRa** – GPIO 16
- **SDR (RTL-SDR)** – GPIO 7
- **USB Hub / internal USB power** – GPIO 23. The internal Wi-Fi and Bluetooth card is on this rail.

Every rail is off at boot unless you choose otherwise with `sudo aiov2_ctl --boot-rail <FEATURE> on`.

These controls allow aiov2_ctl to:
- toggle individual devices
- measure their power cost by comparing battery load before and after toggling
- ensure peripherals aren’t drawing power when they aren’t in use

---
## GPIO State Interpretation

GPIO on/off state is interpreted in this order:

1. `hi` => ON (`pinctrl` source)
2. `lo` => OFF (`pinctrl` source)
3. `--` (not driven) => per-pin boot fallback (`boot_default` source)

This keeps runtime state truth-first and only uses boot fallback when the pin is not actively driven.

### Debug source labels

To show source labels in status output:

```bash
AIOV2_CTL_DEBUG=1 aiov2_ctl --status
```

Source labels:

- `(pinctrl)` from explicit `hi/lo`
- `(boot_default)` from `--` fallback
- `(unknown)` unparseable/failed read path

---
## Typical Use Cases

Measuring feature power is helpful to:
- determine idle vs active power draw for SDR, LoRa, GPS, USB peripherals
- optimize battery life for field deployments
- verify that hardware power gating actually cuts load

---

## Measuring a rail

```bash
aiov2_ctl --measure SDR
```

Compares the battery's draw with the rail off and on. Run it on battery. In the Ops app the same measurement is the **Measure** button on the Overview, or `Shift` with the rail's key, and the results are kept on Settings > Power. See [CLI Usage](CLI-Usage) for the options and how to read the result.

---

## Summary

| Metric        | Meaning |
|---------------|---------|
| **Battery current** | Primary source for power calculations |
| **<0.05 W deltas** | Treated as noise |
| **GPIO power control** | On/off control for major peripherals |
| **USB & regulator rails** | Ignored for net system power |

This approach gives you **trustworthy, hardware-rooted insights** into how your AIO board and uConsole are consuming energy — far more accurate than guessing from USB or regulator readings alone.

