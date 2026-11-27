#!/usr/bin/env python3
"""
The wiki's pictures: lossless WebP files that live in the wiki itself, and the list of them kept here.

The pictures are not in this repository. They are in the project's GitHub wiki, under img/, and the installer
downloads them only for someone who asks for them. What is kept here is docs/wiki/pictures.json: each picture's
path, size and SHA-256, which is how the link checker knows a picture exists and how a download is checked.

    python3 tools/wiki-pictures.py convert SRC DEST     PNGs under SRC/img become lossless WebP under DEST/img
    python3 tools/wiki-pictures.py list DIR             print the list for the pictures under DIR/img, as JSON

Lossless is on purpose: these are screenshots and pixel art, where lossless WebP is both exact and smaller
than a lossy one. Converting needs Pillow; listing needs nothing.
"""
import hashlib
import json
import os
import sys


def picture_list(folder):
    """{'img/…/name.webp': {'bytes': n, 'sha256': '…'}} for every WebP under `folder`/img, in path order."""
    out = {}
    top = os.path.join(folder, "img")
    for root, dirs, names in os.walk(top):
        dirs.sort()
        for name in sorted(names):
            if not name.endswith(".webp"):
                continue
            full = os.path.join(root, name)
            with open(full, "rb") as fh:
                data = fh.read()
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            out[rel] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return out


def webp_name(path):
    """The WebP name a picture gets: the same path with its ending changed."""
    return os.path.splitext(path)[0] + ".webp"


def convert(src, dest):
    """Every PNG under `src`/img written as lossless WebP under `dest`/img. Returns (count, bytes before,
    bytes after). The picture is read back and compared, so a file that is not exact is never left behind."""
    from PIL import Image
    count = before = after = 0
    top = os.path.join(src, "img")
    for root, dirs, names in os.walk(top):
        dirs.sort()
        for name in sorted(names):
            if not name.lower().endswith(".png"):
                continue
            full = os.path.join(root, name)
            target = os.path.join(dest, webp_name(os.path.relpath(full, src)))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with Image.open(full) as im:
                im = im.convert("RGBA")
                im.save(target, "WEBP", lossless=True, quality=100, method=6, exact=True)
                with Image.open(target) as back:
                    if back.convert("RGBA").tobytes() != im.tobytes():
                        os.remove(target)
                        raise SystemExit(f"{full}: the WebP is not pixel for pixel the same; nothing kept")
            count += 1
            before += os.path.getsize(full)
            after += os.path.getsize(target)
    return count, before, after


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == "convert":
        count, before, after = convert(argv[1], argv[2])
        print(f"{count} pictures: {before / 1e6:.2f} MB of PNG became {after / 1e6:.2f} MB of WebP")
        return 0
    if len(argv) == 2 and argv[0] == "list":
        print(json.dumps(picture_list(argv[1]), indent=1, sort_keys=True))
        return 0
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())

