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

