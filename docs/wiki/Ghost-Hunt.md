The **Ghost hunt** is an optional tab in the [Ops App](Ops-App), for fun only. It turns readings the app already has into playful meters with spooky names.

> **It cannot detect ghosts, or anything else.** Every meter is a real radio measurement, and the tab always shows, under each playful name, what is really being measured and its real value. A session it records is entertainment, not evidence of anything.

It is off unless you install it. Without it, nothing of it shows, runs or is mentioned in the app.

---

## Installing and removing it

It is the installer's `ghost` feature, titled **Ghost hunt (for fun)**. It is not in the usual set, so you ask for it:

```bash
sudo aiov2_ctl --install --only ghost
```

or tick it in `sudo aiov2_ctl --install --pick`, or in the installer window (`aiov2_ctl --installer`). It needs the ops app, and installs no packages: it writes one empty file, `~/.local/share/aiov2_ctl/ghost/enabled`, as you. Restart the ops app and the tab appears after Help (and after Wiki, if you have it).

To remove it:

```bash
sudo aiov2_ctl --uninstall --only ghost
```

That removes only the `enabled` file. Any sessions you recorded are left where they are until you delete them (see below).

---

## What it uses

Only what the app's radios are already hearing, read from the app's own pages the same way the **Signals** screensaver reads them: Wi-Fi networks, Bluetooth devices, mesh nodes and GPS satellites. It never switches a rail, starts a scan, a service or a radio, and never runs a command. If a radio is off, its band says so; if nothing at all is being received, the tab says that too.

The software radio (SDR) is not used.

---

## The screen

- **The visualiser** on the left is the Signals screensaver's picture: one band per source, a bar per thing heard, its height the signal strength. A bar that has just jumped is drawn brighter for a moment. That is all it means: the strength changed.
- **Four meters** on the right. Each has its playful name and words, a pixel bar, and underneath the line that starts **Really:**, which says what it really measures and gives the number.
- **Buttons** along the bottom: **Record** / **Stop recording**, **Open session**, **Play** / **Pause**, **Step back**, **Step on** and **Back to live**. Move between them with the D-pad or arrow keys and press A or Enter.

### What each meter really measures

| Meter | Really |
|---|---|
| ENERGY | The average change in the signal strengths it can already hear over the last 10 seconds, in dB. Each band's strongest reading is compared with its strongest, second with second, and so on. Interference, a transmitter changing power, or anything moving near a radio path all cause it. |
| PRESENCES | How many things the radios can hear right now, with the breakdown, for example "14 Wi-Fi networks, 3 Bluetooth devices, 2 mesh nodes". Only the name is playful. |
| ARRIVALS | How many things are heard now that were not heard a minute ago, and how many were heard then but not now: devices coming into and going out of radio range, or switching on and off. |
| STILLNESS | Seconds since any signal strength changed by more than 3 dB, or the number of things heard changed. |

None of them says anything about people, presence, position, direction or distance.

---

## Sessions

**Record** writes a session: one line every 2 seconds holding the time, how many things each source hears and their strengths (rounded), and the four meters. The first line says what the file is and *entertainment: not evidence of anything*. No device address, name or position is ever written.

Sessions are kept only on the device, in

```
~/.local/share/aiov2_ctl/ghost/sessions/
```

The folder can be opened only by you (0700) and each file read only by you (0600). Nothing is sent anywhere.

**Open session** lists the saved sessions, newest first; choose one with A or Enter. The visualiser and meters then show the recording, marked **REPLAY** with the time it was recorded. **Play** runs it at its own speed, **Step back** and **Step on** go a line at a time, and **Back to live** returns to what the radios hear now.

### Deleting sessions

Delete one session:

```bash
rm ~/.local/share/aiov2_ctl/ghost/sessions/ghost-20261128-140322.jsonl
```

or all of them:

```bash
rm -r ~/.local/share/aiov2_ctl/ghost/sessions
```

