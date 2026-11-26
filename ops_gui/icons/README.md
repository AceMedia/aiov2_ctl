AceMedia Host pixel icons (bare variants, PNG at 32 and 64 px), from
https://acemedia.host/icons/ (ace-pixel-icons-1.1.zip). The sun and moon on the theme
button are not part of the set; they are drawn in code in ops_gui/data.py on the same
pixel grid.

bluetooth-bare.png is not part of the set either: it was drawn for this app on the same 16 by 16 grid
(a blue badge with the Bluetooth mark) and scaled by whole numbers to 32 and 64 px.

## Where each icon is used

A tab has its icon in two places, and tests/test_icon_parity.py fails if either is missing:

- the tab bar: `TAB_ICONS` in ops_gui/data.py names the PNG here (`<name>-bare.png`, in both 32/ and 64/);
- the Help page's tab list: `TAB_ICONS` in ops_gui/helpview.py holds a 16 by 16 text bitmap, drawn for this app
  (no third-party licence) in two colours so it follows the theme. It is a plain glyph of the same subject as the
  PNG, not a copy of it.

| Tab | PNG | Help bitmap |
| --- | --- | --- |
| Home | heartbeat | house |
| Overview | blocks | four tiles |
| Everything | growth | a line climbing on a pair of axes |
| Wi-Fi | globe | signal arcs |
| Bluetooth | bluetooth (drawn for this app) | Bluetooth mark |
| Network | server | wired tree |
| Marauder | shield | shield |
| GPS | compass | compass dial and needle |
| LoRa | email | envelope |
| SDR | search | magnifying glass |
| Flipper | code | the Flipper's outline |
| Settings | cog | cog |
| Help | notes | question mark |

The rail rows on Overview reuse compass, email, search and server (`RAIL_INFO` in ops_gui/data.py).

Not used by anything yet, kept from the set for tabs to come: backup, brand, cache, camera, check, database,
domain, human, import, lock, media, migration, people, rocket.
