# Design guide: the pixel look of the app

One look runs through the whole native app (`ops_gui`): Debian red and orange on near black (or paper in light
mode), chamfered corners, and the Spleen pixel font. This page says where each piece lives so you can change it.

## Where things are

| What | File | Change this to... |
|---|---|---|
| Colours for every widget | `ops_gui/style.py` `TOKENS` | recolour the whole app, dark and light |
| The stylesheet | `ops_gui/style.py` `stylesheet()` | change spacing, hover and the tab shapes |
| Cut corner images | `ops_gui/skin.py` `SPECS` | change the corner size, which corners are cut, or the fill and line colours of a shape |
| Home screen palette and layout | `ops_gui/pixel.py` `PALETTES`, `layout()` | move or resize blocks on the Home tab |
| Home screen drawing | `ops_gui/home_panel.py` | change how a block looks |
| Pixel font for headings | `ops_gui/style.py` `pixelise_sheet()` | change which headings use it |
| Terminal drawer | `ops_gui/drawer.py` | the activity log and shell panel |

## Rules that keep it consistent

- **Fonts.** Spleen is only crisp at 12, 16, 24, 32 and 64 pixels (the packages are `fonts-spleen`). Use 24 for
  anything you read at arm's length, 32 for values, 64 for the clock and battery, and 16 only for small titles and
  units. Never anything between those sizes. Tabs are 24, sub tabs 16. Headings (bold, or 14pt and up in an inline
  style) are converted automatically by `pixelise_titles()` in `app.py`; plain body text stays in the normal font.
- **Corners.** Cards, rows, tiles, buttons, fields, tabs and the tab pane get their cut corners from images drawn in
  `skin.py` and stretched by the stylesheet (`border-image`). Give a new widget an object name like `card` or `row`
  and it picks the look up. The Home screen draws the same shape itself (`panel()` in `home_panel.py`).
- **Crisp text.** Qt smooths text by default, which makes the pixel font look soft. `style.crisp_fonts(root)` gives every
  widget that asks for Spleen in its style sheet, and every tab bar, an unsmoothed font like the Home screen's. Call it on
  any new page you add (the app already does for the built in pages).
- **No rounded corners.** Everything uses the diagonal cut (top left and bottom right), including tabs, lists, chips and
  the slide switches. Dividers in the status bar are orange slashes. If you add a widget with its own style sheet, use a
  skin image (`border-image`) instead of `border-radius`.
- **Cheap to draw.** Static things (frames, labels, backgrounds) are drawn once into a pixmap. Dynamic things repaint
  only their own rectangle, and only when their text changed. Animations use a timer that stops when nothing is
  moving. Nothing uses effects, gradients at runtime or antialiasing.
- **Colour means something.** Green is fine, yellow needs a look, red needs attention, cyan is a live value, orange is
  a label. Temperature runs blue to green to red (`temp_colour()` in `pixel.py`).

## Common jobs

- **Add a rack row on Home:** add the name to `RACK_NAMES` and a row to `rack_rows()` in `pixel.py`, then change the
  `6` in `layout()` (rack rows) so the rows divide the panel.
- **Add a vital:** add it to `vital_rows()` and `SPARK_RANGES` in `pixel.py`; `layout()` divides the panel by the
  number of rows.
- **Add a ticker message:** add a tuple to `ticker_messages()`; warnings are marked pinned.
- **Add a tab:** add it to `tab_names()` in `data.py`, a page in `app.py` `pages`, and an icon in `TAB_ICONS`.
- **Test a change:** every pure helper has a test in `tests/`. Run `python3 -m unittest discover -s tests`.
  To see it, launch `python3 -m ops_gui --tab Home` on the uConsole.
