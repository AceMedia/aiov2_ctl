# Wi-Fi and Bluetooth

Everything here listens only. Nothing deauthenticates, injects, spoofs or transmits.

## The radios

```bash
aiov2_ctl --wifi radios [--json]
```

| Name | What it is |
|---|---|
| `wlan0` | The onboard Broadcom radio (`brcmfmac`). |
| `wlan1` | The MediaTek MT7921 on the AIO board's internal USB (`mt7921u`, `0e8d:7961`). It is powered by the USB rail. |
| other | Any other USB adapter shows as `external-usb`. |

## Scanning

```bash
aiov2_ctl --wifi scan [--iface wlan0|wlan1] [--json] [--save]
```

An ordinary managed-mode scan on each radio through NetworkManager, so it needs no root. If a Flipper's Wi-Fi dev board is running the USB-UART Bridge app and answers, its access points are added as a third radio called `flipper-marauder`. With no Flipper plugged in, that port is never opened.

- `--json` prints one entry per network and radio: name, BSSID, channel, frequency, signal, security and radio. Hidden networks have an empty name.
- NetworkManager gives a 0 to 100 signal figure, so `signal_dbm` is an estimate worked out from it.
- `--save` writes the JSON to `~/aiov2-captures` (folder 700, file 600, never overwrites). The file holds your neighbours' network names, so treat it as private.

## Listening on one channel

```bash
sudo aiov2_ctl --wifi-listen --iface wlan1 --channel 6 --seconds 60 --json
sudo aiov2_ctl --wifi-listen --restore          # put the radio back to normal if anything went wrong
```

Puts a radio into monitor mode, listens on one channel and prints, once a second, how many frames it heard, the average signal strength and the signal per sender.

- It never transmits.
- It refuses a radio that is connected to a network.
- It always puts the radio back when it stops, including on Ctrl+C.
- It needs root and the `iw` package. The `listening` feature of the installer installs `iw` and the rules that let `--wifi-listen`, `--wifi-survey` and `--bt-scan` run without a password.

Signal strength says how strongly a sender was heard. It does not give a direction or a position.

## Surveying every channel

```bash
sudo aiov2_ctl --wifi-survey [--iface wlan1] [--band 2g|5g|all] [--seconds 3600] [--pcap] [--json]
```

Puts the internal radio into monitor mode, hops between channels and lists what it hears:

- access points
- stations (devices using a network)
- probe requests (devices looking for a network)
- deauthentication floods. These are detected and reported, never sent.
- how busy each channel is

It never transmits, and the radio is returned to normal when it stops. `--pcap` also saves a capture under `~/aiov2-captures`, with data frames cut after their header so no payload is kept.

In the Ops app this is the Survey page of the Wi-Fi tab, which can also keep a wardriving log (each network with the position the uConsole heard it best from) and export it as a WiGLE CSV. That position is the uConsole's, not the network's.

## Bluetooth

```bash
sudo aiov2_ctl --bt-scan [--adapter hci1] [--seconds 30] [--json]
```

Lists the Bluetooth Low Energy devices advertising nearby: name, address, maker and signal strength.

- Passive scanning only. It listens to what devices broadcast and never sends a scan request, connects, pairs or advertises.
- Scanning is switched off again when it stops.
- It uses the internal card's Bluetooth radio, so it needs the USB rail on.
- It cannot tell you where a device is, how far away it is or who it belongs to. Phones, watches and earbuds change their address every few minutes, so one device can appear several times.

## The wired network

```bash
aiov2_ctl --net status [--json]
aiov2_ctl --net neighbors [--json]
sudo aiov2_ctl --net listen [--iface eth0] [--seconds 30] [--json]
sudo aiov2_ctl --net sweep [--iface eth0] --yes [--json]
aiov2_ctl --net ports HOST [--ports 22,80,443] --yes [--json]
```

- `status` and `neighbors` only read: the link, and the hosts the system already knows.
- `listen` is passive. It hears what hosts announce and sends nothing.
- `sweep` sends one ARP request to each address on your own subnet and lists who answers. `ports` opens ordinary TCP connections to one host. Both send traffic, so they need `--yes` and only work on private addresses of a network this machine is on.
- Nothing here attacks, spoofs or guesses passwords. Only sweep or check a network you own or have permission to look at.

## Monitor mode with your own tools, without losing the internet

```bash
sudo aiov2_ctl --wifi-monitor on
sudo aiov2_ctl --wifi-monitor off
aiov2_ctl --wifi-monitor status
```

`airmon-ng check kill` stops NetworkManager, which also takes the onboard Wi-Fi offline. `--wifi-monitor on` tells NetworkManager to leave the USB Wi-Fi alone instead, without dropping connections, so you can skip `check kill`. While it is on, NetworkManager will not connect `wlan1` to networks; `off` hands it back.

## In the Ops app

The Wi-Fi tab has pages for scans, channels, Survey and Sense. A Bluetooth tab appears when the internal card is there, and a Network tab when there is a wired port. The card is on the USB rail, so Survey, Sense and Bluetooth need that rail on. See [Ops App](Ops-App).
