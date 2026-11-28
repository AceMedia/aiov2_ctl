# LoRa Mesh

The AIO board's LoRa radio runs a Meshtastic mesh through the `meshtasticd` service. The `mesh` feature of the installer installs it.

`meshtasticd` does not come from Debian. `sudo aiov2_ctl --add-apps` sets up the Meshtastic package source, picking the build that matches your system (Debian 12 or 13).

## Switching it on

```bash
aiov2_ctl LORA on
aiov2_ctl LORA off
```

Or tap the LoRa rail in the tray or the Ops app, or press `M` in the app.

`meshtasticd` starts when the LoRa rail comes on and is not started at boot, so the rail decides when the radio runs. To change that:

```bash
aiov2_ctl --mesh-on-boot status
aiov2_ctl --mesh-on-boot          # also start it at boot
aiov2_ctl --mesh-off-boot         # the default
```

The tray's **App Services** menu shows whether `meshtasticd` is running and can start, stop or restart it.

## Before you transmit

Meshtastic must be set to your region (for example `EU_868`, `US`, `ANZ`). Set it in the Meshtastic app before sending anything; see [Applications](Applications).

## In the Ops app

The LoRa tab is there while the LoRa rail is on. It has three pages.

### Mesh

- Whether the mesh is reachable, and the next step when it is not.
- The nodes heard: this node first, marked "(this node)", then the rest by when each was last heard. The mesh service remembers nodes between runs, so a node can be listed that has not been heard for a long time; the list says how long ago.
- Names get the width the other columns do not need, so long names are shown in full where there is room.
- The **SNR / RSSI** column gives the last signal figures heard from each node, for example "+6.2 dB  -91 dBm", with "-" for a figure that is not known. Its tooltip says which is which and how old the reading is. Signal figures do not say how far away a node is.
- A live feed of what is heard.
- A message box, with a **Send on** list of the channels set up on the radio, the primary first and disabled ones left out. If the connection does not give that list, it shows the channels heard lately instead. Nothing is written to the radio.
- The channel you choose stays chosen. It only changes by itself when **Follow** is on and **Lock** is off.
- If the connection to the mesh is lost, the tab says so, stops offering to send and connects again.
- **Export history** saves the nodes, positions and messages to a new file that only your account can read. A second export never overwrites the first.

Receiving is the default. Sending has to be switched on with **Allow sending** each time you open the tab, because a message goes out over the air.

After Send, the tab says the message was handed to the radio. Whether anyone received it is not known. With **Ask for acknowledgement** switched on in Settings > LoRa (it is off by default), each message asks for an acknowledgement and the mesh retries it when none comes, so the radio sends more. The message then shows as acknowledged, failed with the reason, or not acknowledged after a minute; an answer that comes later still counts.

[![LoRa, Mesh](img/app/tab-lora-mesh.webp)](img/app/tab-lora-mesh.webp)

The node names are covered in this picture and the next.

### Messages

Every text message and check-in heard or sent, newest at the bottom, with a filter by channel. They are kept apart from telemetry and position packets, so a burst of those cannot push them out; up to 300 are kept, in memory only. The view opens at the newest message, and while you read older ones it keeps your place when new ones arrive. Each message you sent shows what became of it, as above.

### Radar

Nodes that have told the mesh their position, drawn around this node by bearing and distance, with a proximity ring you can widen or narrow.

- Green: inside the ring. Amber: outside. Grey: the position is more than 15 minutes old, or of unknown age, even if the node has been heard since.
- Each dot is labelled with the age of its position, and resting the pointer on the radar lists every node with its distance and the age of its position.
- It needs this node's own position (a GPS fix or a fixed position).
- A node appears only if it sends its position. Nothing here is worked out from signal strength.
- The Alerts list beside it names nodes inside the ring and nodes not heard for a while.

[![LoRa, Radar](img/app/tab-lora-radar.webp)](img/app/tab-lora-radar.webp)

### Notices

Settings > Notifications can tell you about new messages, check-ins and, if you ask, new nodes while you are on another tab. For that the app keeps its connection to the mesh open while the LoRa rail is on. It never sends anything for this.

## In the Ops console

The console has a LoRa panel with keys for sending a message, sending a position check-in, changing channel, the proximity ring and exporting history. See [Ops Console](Ops-Console).

## A radio on USB serial

When the app or console reaches a radio over USB serial rather than through `meshtasticd`, it looks at `ttyACM0`, `ttyUSB0`, `ttyACM1` and `ttyUSB1`, leaves a Flipper's or ESP32 board's port alone, and opens a port only when its USB identity is a chip Meshtastic boards use: Espressif, Silicon Labs CP210x, WCH CH340 or CH9102, Adafruit nRF52 or Raspberry Pi RP2040. It opens that port by name, never by guessing. The status says which port it chose and why, or why no port qualified.

## If the mesh is not reachable

- **The LoRa rail is off.** Switch it on.
- **`meshtasticd` is stopped.** Turn the LoRa rail off and on again, or run `sudo systemctl start meshtasticd`.
- **The Python library is missing.** `pip3 install --break-system-packages meshtastic`
- **The package cannot be found.** Run `sudo aiov2_ctl --add-apps` to set up the Meshtastic package source, then install again.
