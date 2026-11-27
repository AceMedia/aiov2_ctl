# docs

What is in this folder.

| Path | What it is |
|---|---|
| `DESIGN.md` | How the code is laid out and why. |
| `wiki/` | The source of the project's GitHub wiki: one `.md` file per page, `_Sidebar.md`, `_Footer.md`, and the pictures in `wiki/img/`. |

The tools for the wiki are in `tools/`. Run them from the top of the repository.

## Edit a wiki page

1. Edit the page in `docs/wiki/`. The file name is the page name, with `-` for spaces.
2. A link to another page is its name without `.md`: `[Installation](Installation)`.
3. A picture goes in `docs/wiki/img/` and is linked as `img/...`.
4. A new page must be added to `docs/wiki/_Sidebar.md`.

Do not rename `CLI‐Usage.md`. GitHub wrote its name with a special hyphen. Links to it use a plain one: `[CLI Usage](CLI-Usage)`.

Look at every picture before you add it. A screenshot can show a user name, a host name, a Wi-Fi name or an address.

## Check it

```sh
python3 tools/wiki-check.py
```

It checks that every link names a page that exists, every picture exists, every `#heading` link names a real heading, and every page is in the sidebar. It prints each fault and exits with 1 if there is one. It does not use the network, so links to other sites are not tested.

The test suite runs the same check:

```sh
python3 -m unittest discover -s tests
```

## Publish it

Commit your change first. Only committed files are published.

```sh
tools/wiki-publish.sh --dry-run    # show what would change
tools/wiki-publish.sh --yes        # publish
```

The script runs the checker and stops if it fails. It then shows the changes. Without `--yes` it stops there.

It publishes to the wiki of this checkout's `origin`. To choose another, add `--repo OWNER/REPO`.

It never force-pushes. It never deletes a page that is only in the wiki. It names such pages and leaves them.

You need to be able to push to the wiki. With `gh` installed and signed in, that works by itself.

## Draw the logo again

The logo and the divider in `docs/wiki/img/app/` are drawn by a script. It needs only Pillow (`python3-pil`).

```sh
python3 tools/logo.py              # writes into docs/wiki/img/app/
python3 tools/logo.py /tmp/logo    # writes into another folder
```

It writes `aio-ops-logo.png`, `aio-ops-logo-full.png` and `divider.png`. Add `--preview FILE.png` to also get a picture of all three on a light page and a dark page.

The font is `tools/fonts/press-start-2p.ttf` (Press Start 2P). Its licence is in `tools/fonts/OFL.txt`.
