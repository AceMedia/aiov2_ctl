# docs

| Path | What it is |
|---|---|
| `DESIGN.md` | The ops app's pixel look: where colours, corners, fonts and the Home screen layout live. |
| `wiki/` | The source of the project's GitHub wiki: one `.md` file per page, `_Sidebar.md`, `_Footer.md`, and `pictures.json`, the list of its pictures. The ops app's Wiki tab reads these same files. |

## The wiki's pictures

The pictures are not in this repository. They are lossless WebP files kept in the wiki itself, under `img/`, so
that someone who installs without them downloads nothing they did not ask for. `wiki/pictures.json` lists each
one with its size and SHA-256. The link checker uses that list to know a picture exists, and the installer uses
it to check each picture it downloads.

Lossless is on purpose: for screenshots and pixel art it is both exact and smaller than a lossy WebP.

## Edit a page

1. Edit the `.md` file in `docs/wiki/`. A link to another page is its file name without `.md`, for example
   `[Installation](Installation)`.
2. Check it:

   ```bash
   python3 tools/wiki-check.py
   ```

   The test suite runs the same check, so a broken link fails the tests.
3. Publish it (see below).

## Add or change a picture

1. Make the PNG, with nothing private in it.
2. Convert it and put it in a clone of the wiki:

   ```bash
   git clone https://github.com/OWNER/REPO.wiki.git /tmp/wiki
   mkdir -p /tmp/new/img/app && cp picture.png /tmp/new/img/app/
   python3 tools/wiki-pictures.py convert /tmp/new /tmp/wiki      # writes /tmp/wiki/img/app/picture.webp
   python3 tools/wiki-pictures.py list /tmp/wiki > docs/wiki/pictures.json
   ```

3. Link it in a page as `img/app/picture.webp`, commit `docs/wiki/`, push the picture in the wiki clone, then
   publish the pages.

## Record a clip

A clip is a short moving picture of the app being used: an animated WebP, which GitHub plays in a page. It is
recorded on a real uConsole and kept with the pictures, under `img/clips/`.

1. Write what the clip does as a script, or use one in `tools/clips/`. A script is one function, `clip(rec)`,
   that drives the app through the recorder: `rec.tab("Overview")`, `rec.dpad("down", "right")`,
   `rec.settings("General")`, `rec.shot(900)`. To show something change over time, `rec.watch(60, every=2)`
   takes a frame every two seconds for a minute and plays them back in three seconds.
2. Record it on the uConsole, from the checkout, in the desktop session:

   ```bash
   python3 tools/wiki-record.py tools/clips/dpad.py /tmp/frames-dpad
   ```

   A recording cannot save a setting or switch a rail, and it swaps the computer's name, its address and its
   network's name for stand-ins before the app starts. It does not hide what a screen lists: stay off the
   Network tab, Wi-Fi scans, GPS positions and mesh nodes, or cover them by hand. For a radio's screen, switch
   its rail on yourself first.
   A clip that starts a scan or a session listens for real: what it hears is kept by the app as usual, and a
   recorded session is saved like any other, so remove it afterwards if it was only for the clip.
3. Turn the frames into the clip, in a clone of the wiki:

   ```bash
   python3 tools/wiki-clip.py /tmp/frames-dpad /tmp/wiki/img/clips/dpad.webp
   python3 tools/wiki-pictures.py list /tmp/wiki > docs/wiki/pictures.json
   ```

   Full size and lossless is the default and, for this app, also the smallest. `OUT.gif` and `OUT.webm` work
   too, for somewhere WebP will not play or for a long clip.
4. **Look at every frame** for anything private. Then link it as `img/clips/dpad.webp`, commit `docs/wiki/`,
   push the clip in the wiki clone, and publish the pages.

In the app's own Wiki tab a clip shows as its first frame.

## Publish the pages

```bash
tools/wiki-publish.sh --dry-run      # what would change; changes nothing
tools/wiki-publish.sh --yes          # show the changes, then commit and push them
```

It publishes only what is committed under `docs/wiki/`, refuses when the checker finds a problem, never
force-pushes, and never deletes a page or picture that is only in the wiki.

## The logo

The logo and the divider are drawn by a script. It needs only Pillow (`python3-pil`).

```bash
python3 tools/logo.py                # writes three WebP files into a folder called logo
python3 tools/logo.py /tmp/wiki/img/app
```

It writes `aio-ops-logo.webp`, `aio-ops-logo-full.webp` and `divider.webp`. Add `--preview FILE.png` to also get a
picture of all three on a light page and a dark one. The font is Press Start 2P, under the SIL Open Font
Licence (`tools/fonts/OFL.txt`).

