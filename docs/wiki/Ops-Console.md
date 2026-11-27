# Ops Console

The Ops console is the terminal version of the Ops app: GPS, SDR and LoRa panels in a text window, for SSH or a small terminal. It is the `console` feature of the installer and uses the same GPS, SDR and mesh code as the app.

## Starting it

- From the Ops app: press `X` to open the drawer, then **CONSOLE**. The app closes, the console opens in a terminal a moment later, and the rails are left exactly as they are.
- From the start menu: "uConsole Ops Console".
- From a terminal, in this tool's checkout (`~/aiov2_ctl-ace` by default):

```bash
python3 ops.py
```

Started this way, it goes through the rails that are on and asks whether to keep each one, and when you quit it lists what is on and offers to switch it off.

| Option | What it does |
|---|---|
| `--no-prompts` | Keep every rail as it is, with no questions on the way in or out. |
| `--debug` | Show a debug panel beside the others. |
| `--upconverter off` / `ham-it-up` / a number of MHz | Set and remember an upconverter offset for the SDR. |

## What it shows

A panel for each rail that is on (GPS, SDR, LoRa), a Flipper panel when a Flipper is plugged in, a Wi-Fi panel when there is a Wi-Fi radio, and a status panel. A rail that is off has no panel and its keys do nothing.

## Keys

| Key | What it does |
|---|---|
| `Q` | quit |
| `O` | move to or from the status panel |
| `L` | Wi-Fi scan (passive) |
| `K` | refresh the Flipper panel, when one is plugged in |
| `I` | the Flipper's screen, when one is plugged in |

### SDR (when the SDR rail is on)

| Key | What it does |
|---|---|
| `A` | audio on or off |
| `/` | next mode |
| `.` and `,` | next or previous band |
| `C` / `Shift`+`C` | next or previous channel |
| `P` / `Shift`+`P` | next or previous preset |
| `N` / `Shift`+`N` | next or previous station |
| Up and Down | tune up or down one step |
| Left and Right | fine tune |
| `[` and `]` | filter narrower or wider |
| `F` / `Shift`+`F` | scan forwards or backwards |
| `Esc` | stop a scan |
| `S` | automatic squelch |
| `V` / `Shift`+`V` | volume up or down |
| `M` | mute |
| `E` | type a frequency (for example `145.5` or `ch19`) |
| `U` | CB channels |
| `H` | upconverter |
| `B` | Bias-T |

Bias-T puts voltage on the antenna lead. Read [SDR](SDR) before pressing `B`.

### LoRa (when the LoRa rail is on)

| Key | What it does |
|---|---|
| `T` | send a text message to the mesh |
| `G` | send a position check-in to the mesh |
| `Y` / `Shift`+`Y` | next or previous mesh channel |
| `J` | jump to the best channel |
| `Z` | follow the active channel |
| `Shift`+`Z` | lock the channel |
| `R` / `Shift`+`R` | proximity ring wider or narrower |
| `W` / `Shift`+`W` | next or previous view |
| `D` / `Shift`+`D` | next or previous node |
| `X` | export history |

`T` and `G` transmit over LoRa. Set your region in Meshtastic first; see [LoRa Mesh](LoRa-Mesh).

## Going back to the app

Quit the console and open "uConsole Ops" from the start menu. The console lets go of the SDR dongle, the serial ports and `gpsd` when it quits.

## If it crashes

The console prints the error and waits for Enter, so you can read or copy it. See [Reporting Problems](Reporting-Problems).
