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
- The nodes heard: this node first, then the rest by when each was last heard. The mesh service remembers nodes between runs, so a node can be listed that has not been heard for a long time; the list says how long ago.
- A live feed of what is heard.
- A message box.

Receiving is the default. Sending has to be switched on with **Allow sending** each time you open the tab, because a message goes out over the air.

[![LoRa, Mesh](img/app/tab-lora-mesh.webp)](img/app/tab-lora-mesh.webp)

The node names are covered in this picture and the next.

### Messages

Every text message and check-in heard or sent, newest at the bottom, with a filter by channel. **Export history** saves it to a file.

### Radar

Nodes that have told the mesh their position, drawn around this node by bearing and distance, with a proximity ring you can widen or narrow.

- Green: inside the ring. Amber: outside. Grey: not heard for 15 minutes.
- It needs this node's own position (a GPS fix or a fixed position).
- A node appears only if it sends its position. Nothing here is worked out from signal strength.
- The Alerts list beside it names nodes inside the ring and nodes not heard for a while.

[![LoRa, Radar](img/app/tab-lora-radar.webp)](img/app/tab-lora-radar.webp)

### Notices

Settings > Notifications can tell you about new messages, check-ins and, if you ask, new nodes while you are on another tab. For that the app keeps its connection to the mesh open while the LoRa rail is on. It never sends anything for this.

## In the Ops console

The console has a LoRa panel with keys for sending a message, sending a position check-in, changing channel, the proximity ring and exporting history. See [Ops Console](Ops-Console).

## If the mesh is not reachable

- **The LoRa rail is off.** Switch it on.
- **`meshtasticd` is stopped.** Turn the LoRa rail off and on again, or run `sudo systemctl start meshtasticd`.
- **The Python library is missing.** `pip3 install --break-system-packages meshtastic`
- **The package cannot be found.** Run `sudo aiov2_ctl --add-apps` to set up the Meshtastic package source, then install again.
