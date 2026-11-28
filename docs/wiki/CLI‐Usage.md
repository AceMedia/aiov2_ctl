**aiov2_ctl** is designed to be used from the command line first.

The CLI gives you direct, low-level visibility into what the AIO v2 board is actually doing - power draw, GPIO state, and how individual peripherals affect the system in real time.

All power readings come from the battery rail, which means you’re seeing what the hardware is genuinely consuming, not what the OS thinks is happening.

---

## Status

```bash
aiov2_ctl --status
```

Prints a one‑shot snapshot of system state:

- GPIO power state for GPS, LoRa, SDR and USB
- Active power source (battery or AC)
- Battery charge percentage
- Charge / discharge direction
- Battery rail voltage, current and power

**Tip:**  
Use this first when diagnosing power or peripheral issues.

---

## Live Power Monitor

```bash
aiov2_ctl --power
```

Runs a live, full‑screen power display (updates every second).

Shows:
- Whether the system is running on battery or AC
- Battery percentage and charge direction
- Real‑time battery voltage, current and power draw

Exit with **Ctrl+C**.

**Tip:**  
Ideal for spotting transient spikes when enabling radios or USB devices.

---

## Watch Mode

```bash
aiov2_ctl --watch
```

Compact, single‑line live output suitable for SSH and tmux.

Displays:
- GPIO ON/OFF state for all peripherals
- Power source
- Battery state and percentage
- Current power draw

**Tip:**  
Best for long‑running observation while working in another pane.

---

## GPIO Control

```bash
aiov2_ctl GPS on
aiov2_ctl SDR off
```

Manually power peripherals via GPIO.

Available features:
- `GPS`
- `LORA`
- `SDR`
- `USB`

Changes take effect immediately.

**Tip:**  
Always power on the required module before launching related software.

---

## Rail Boot State Commands

Set per-feature rail state to apply at system boot:

```bash
aiov2_ctl --boot-rail <FEATURE> on
aiov2_ctl --boot-rail <FEATURE> off
aiov2_ctl --boot-rail <FEATURE> status
aiov2_ctl --boot-rails-status
```

Supported features:

- `GPS`
- `LORA`
- `SDR`
- `USB`

Examples:

```bash
aiov2_ctl --boot-rail SDR on
aiov2_ctl --boot-rail LORA off
aiov2_ctl --boot-rail SDR status
aiov2_ctl --boot-rails-status
```

Notes:

- `on/off` updates effective boot configuration used by the boot service.
- Settings are applied at startup by `aiov2-rails-boot.service`.

---

## Measure Power Delta

```bash
aiov2_ctl --measure SDR
```

Measures the **real power impact** of enabling or disabling a single hardware feature.

Unlike rail or USB estimates, this uses **battery current as the truth source**, which captures everything downstream: regulators, inefficiencies, startup spikes, and real load.

---

### What It Does

The measurement process is deliberately simple and repeatable:

1. Samples baseline battery power with the feature **OFF**
2. Toggles the feature **ON**
3. Waits for the system to settle
4. Samples battery power again
5. Reports the delta (Δ) in watts

This gives you a *net cost* of that feature under real-world conditions.

---

### Basic Example

```bash
aiov2_ctl --measure SDR
```

Typical output:

```
Measure: SDR (GPIO7)
Power source: Battery
Current state: OFF

Results
----------------
OFF: 3.21 W
ON : 3.87 W
Δ   +0.66 W
```

Interpretation:
- Enabling the SDR increases total system draw by ~0.66 W
- This includes the SDR itself **and** any supporting power overhead

---

### Available Sub‑Options

```bash
aiov2_ctl --measure <FEATURE> [--seconds N] [--interval S] [--settle S]
```

#### `--seconds N`
How long to sample power in each state.

- Default: `3`
- Use higher values for noisy loads (Wi‑Fi, SDR)

Example:
```bash
aiov2_ctl --measure SDR --seconds 6
```

#### `--interval S`
Time between individual samples (seconds).

- Default: `0.2`
- Lower values = more samples, slightly noisier
- Higher values = smoother averages

Example:
```bash
aiov2_ctl --measure USB --interval 0.5
```

#### `--settle S`
Delay after toggling the feature before sampling begins.

- Default: `1`
- Useful for radios and USB devices that spike briefly on startup

Example:
```bash
aiov2_ctl --measure GPS --settle 2
```

---

### Feature‑Specific Tips

**SDR**
- Expect higher deltas when actively receiving
- Ensure no other SDR application is running
- Allow time to settle before sampling

**LoRa**
- Extremely low idle draw
- Deltas may sit near the noise floor unless transmitting

**GPS**
- Initial lock can spike power briefly
- Longer settle times give more representative readings

**USB**
- Captures *everything* on the USB rail
- Useful for diagnosing brownouts and instability

---

### Interpreting Results

- **< 0.05 W**  
  Below the noise floor. Treat as zero.

- **0.05 – 0.2 W**  
  Very low‑power peripheral or idle radio.

- **0.3 – 1.0 W**  
  Active radios, SDR, or USB devices.

- **> 1.0 W**  
  Sustained heavy load or combined peripherals.

---

### Best Practices

- Run on **battery**, not AC  
  Charging masks real consumption.

- Close background applications  
  Especially SDR, GPS, and radio services.

- Repeat measurements  
  Consistency matters more than single readings.

- Trust direction as well as magnitude  
  The sign (+/–) tells you whether the feature adds or removes load.

---

### Why This Exists

This mode exists to answer practical questions:

- What does this feature *actually* cost?
- Why does enabling Bluetooth cause instability?
- Is a brownout software or power‑budget related?

If the battery current says it happened — it happened.


---

## GUI

```bash
aiov2_ctl --gui
```

Launches the system tray interface.

- Right‑click: toggle peripherals, rails on boot, boot device, services, power, updates
- Left‑click: compact status window
- Updates once per second

See [GUI](GUI).

---

## Autostart

```bash
aiov2_ctl --autostart
aiov2_ctl --no-autostart
```

Enable or disable GUI autostart on login (user‑level).

---

## Help

```bash
aiov2_ctl --help
```

Prints the full command reference and post‑install tips.

---

## Notes on Power Accuracy

- Battery current is the truth source
- USB rail and reported adapter values are ignored
- Short spikes matter more than averages
- Noise floor is approximately **±0.05 W**

---

## Installing, removing and updating

Full details: [Installation](Installation), [Uninstall](Uninstall), [Updates and Channels](Updates-and-Channels).

```bash
aiov2_ctl --features                       # what can be installed, what is on by default, what is here now
aiov2_ctl --features --json                # the same as data
aiov2_ctl --install --dry-run              # the plan, in plain words; changes nothing and needs no root
sudo aiov2_ctl --install                   # a new machine: the usual set. One already set up: what it has, brought up to date
sudo aiov2_ctl --install --all             # everything, including the aircraft receiver and tray at login
sudo aiov2_ctl --install --only core,tray  # just these (and what they need)
sudo aiov2_ctl --install --without app,console
sudo aiov2_ctl --install --pick            # tick the features you want on screen, then see the plan
sudo aiov2_ctl --install --yes             # do not ask; needed when there is no terminal to ask on
aiov2_ctl --installer                      # the same as a window; run it as yourself
sudo aiov2_ctl --repair                    # put back what is recorded as installed and has gone missing
sudo aiov2_ctl --apply --keep core,tray,gps  # make the installed features exactly these: the rest are removed
sudo aiov2_ctl --uninstall --only flipper  # remove one feature, and whatever cannot work without it
sudo aiov2_ctl --uninstall                 # remove everything this tool installed
aiov2_ctl --update                         # run it as yourself; it asks before it uses root
aiov2_ctl --channel                        # which channel this is on, and what is waiting
aiov2_ctl --channel beta                   # switch to beta, then update
aiov2_ctl --channel stable                 # and back
```

## Health check

```bash
aiov2_ctl --doctor
```

Read only. See [Health Check](Health-Check).

## Boot device

```bash
aiov2_ctl --boot-device status
sudo aiov2_ctl --boot-device sd
sudo aiov2_ctl --boot-device nvme
sudo aiov2_ctl --boot-device toggle
```

Sets whether the CM5 bootloader tries SD/eMMC or NVMe first, by editing `BOOT_ORDER` with `rpi-eeprom-config --apply` (`sd` is `0xf61`, `nvme` is `0xf16`). It needs the `rpi-eeprom` package and takes effect at the next reboot. `status` needs no root.

## Radios and companions

```bash
aiov2_ctl --wifi radios [--json]
aiov2_ctl --wifi scan [--iface wlan0|wlan1] [--json] [--save]
sudo aiov2_ctl --wifi-listen --iface wlan1 --channel 6 --seconds 60 --json
sudo aiov2_ctl --wifi-listen --restore
sudo aiov2_ctl --wifi-monitor on|off
aiov2_ctl --wifi-monitor status
sudo aiov2_ctl --wifi-survey [--band 2g|5g|all] [--seconds N] [--pcap] [--json]
sudo aiov2_ctl --bt-scan [--seconds 30] [--json]
aiov2_ctl --net status | neighbors [--json]
sudo aiov2_ctl --net listen [--seconds 30] [--json]
aiov2_ctl --companions [--json]
aiov2_ctl --flipper info | ls [/ext] | apps | open <APP>
aiov2_ctl --flipper put LOCAL REMOTE [--force] | get REMOTE [LOCAL] | mkdir REMOTE
aiov2_ctl --flipper clock [set --yes]
aiov2_ctl --marauder info | scan [all|beacon] [--seconds N] | list [aps|stations] | channel [N] | stop
```

See [Wi-Fi and Bluetooth](Wi-Fi-and-Bluetooth) and [Companion Devices](Companion-Devices).

## SDR helpers

```bash
sudo aiov2_ctl --sdr-biastee on|off
aiov2_ctl --sdr-biastee status
```

Powers the SDR antenna input (bias tee) each time `readsb` starts, for an active antenna or inline LNA. Only switch it on with an antenna or LNA that expects DC power; it stays on until the SDR loses power.

```bash
sudo aiov2_ctl --fix-sdr
```

Frees the RTL-SDR from the kernel's DVB television driver when radio programs cannot open it. See [SDR](SDR).

## Apps, services and the clock

```bash
sudo aiov2_ctl --add-apps          # Meshtastic, SDR++, PyGPSClient, tar1090
sudo aiov2_ctl --remove-apps
aiov2_ctl --mesh-on-boot status    # is meshtasticd started at boot?
aiov2_ctl --mesh-on-boot           # start it at boot
aiov2_ctl --mesh-off-boot          # the default: it starts with the LoRa rail
sudo aiov2_ctl --sync-rtc          # write the system time to the hardware clock
aiov2_ctl --check-update           # look for an update and offer to install it
```

Only run `--sync-rtc` once the system time is right, for example after it has been set from the network. See [Applications](Applications), [LoRa Mesh](LoRa-Mesh) and [GPS](GPS).

## Privileges

- Anything run as root only ever executes the installed copy, `/usr/local/bin/aiov2_ctl`, which only root can change. A boot-rail or similar command run from a checkout in your home refuses and tells you to install first.
- `sudo aiov2_ctl --setup-sudoers` adds `/etc/sudoers.d/aiov2_ctl` so the tray can change boot rails, the boot device and the `meshtasticd` and `readsb` services without a password. The rules are exact commands with no wildcards and are checked with `visudo` before they are written. `sudo aiov2_ctl --setup-sudoers --remove` takes them away.
- Set `"require_signed_updates": true` in the system config to refuse an update whose newest commit is not validly signed.
