#!/usr/bin/env python3
"""
Turn the frames tools/wiki-record.py made into one moving picture for the wiki.

    python3 tools/wiki-clip.py FRAMES OUT.webp            an animated WebP (the usual choice; GitHub plays it)
    python3 tools/wiki-clip.py FRAMES OUT.gif             a GIF, for somewhere WebP will not play
    python3 tools/wiki-clip.py FRAMES OUT.webm            a WebM video, for a long clip (needs ffmpeg)

    --width N         scale to N pixels wide (default 0: keep the size)
    --crop-bottom N   cut N pixels off the bottom first (default 24: the desktop's taskbar)
    --hold-last MS    how long the last frame stays before the clip starts again (default 1500)
    --ms MS           play every frame for MS, whatever the recording said
    --quality N       lossy WebP at quality N (default 100: lossless)

Full size and lossless is the default because, for this app, it is also the smallest: between two frames
only a few pixels change, and unscaled they change exactly. Three clips of 8 to 26 frames came to 79, 186 and
207 KB that way, and to 258, 149 and 231 KB scaled to 800 wide at quality 80. Scale only for a clip whose
whole picture changes every frame.

Each frame plays for as long as the recording said (frames.json beside the frames). Frames that are the same
as the one before are folded into it, so a pause costs nothing. The ending of OUT picks the kind of file.
WebP and GIF need Pillow.
"""
import json
import os
import subprocess
import sys

DEFAULT_MS = 400


def frame_plan(names, table=None, hold_last=1500, override=None):
    """[(file, how long it plays in ms)] in order. `names` are the frame files, `table` what the recording
    said ({file: ms}), `override` one length for every frame. The last frame is held for at least `hold_last`."""
    table = table or {}
    plan = [(name, int(override if override else table.get(name, DEFAULT_MS))) for name in sorted(names)]
    if plan and hold_last:
        plan[-1] = (plan[-1][0], max(plan[-1][1], int(hold_last)))
    return plan


def fit(size, width=0, crop_bottom=24):
    """((left, top, right, bottom) to keep, (width, height) to scale to). Sizes are made even, which video
    needs, and a picture is never made bigger than it is."""
    w, h = size
    keep_h = max(2, h - max(0, crop_bottom))
    if not width or width >= w:
        out_w, out_h = w, keep_h
    else:
        out_w, out_h = width, round(keep_h * width / w)
    return (0, 0, w, keep_h), (out_w - out_w % 2, out_h - out_h % 2)


def folded(plan, same):
    """The plan with every frame that looks the same as the one before folded into it: its time is added on.
    `same(a, b)` says whether two frame files show the same thing."""
    out = []
    for name, ms in plan:
        if out and same(out[-1][0], name):
            out[-1] = (out[-1][0], out[-1][1] + ms)
        else:
            out.append((name, ms))
    return out


def kind_of(path):
    ending = os.path.splitext(path)[1].lower()
    return {".webp": "webp", ".gif": "gif", ".webm": "webm"}.get(ending)


def read_table(folder):
    try:
        with open(os.path.join(folder, "frames.json")) as f:
            return {row["file"]: row["ms"] for row in json.load(f)["frames"]}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def make(folder, out, width=0, crop_bottom=24, hold_last=1500, override=None, quality=100):
    from PIL import Image
    kind = kind_of(out)
    if kind is None:
        raise SystemExit("The output must end in .webp, .gif or .webm.")
    names = [n for n in os.listdir(folder) if n.endswith(".png")]
    if not names:
        raise SystemExit(f"No frames in {folder}.")
    cache = {}

    def frame(name):
        if name not in cache:
            im = Image.open(os.path.join(folder, name)).convert("RGB")
            box, size = fit(im.size, width, crop_bottom)
            im = im.crop(box)
            cache[name] = im if im.size == size else im.resize(size, Image.LANCZOS)
        return cache[name]

    plan = folded(frame_plan(names, read_table(folder), hold_last, override),
                  lambda a, b: frame(a).tobytes() == frame(b).tobytes())
    images, times = [frame(n) for n, _ in plan], [ms for _, ms in plan]
    if kind == "webp":
        extra = {"lossless": True} if quality >= 100 else {"quality": quality}
        images[0].save(out, save_all=True, append_images=images[1:], duration=times, loop=0, method=6, **extra)
    elif kind == "gif":
        images[0].save(out, save_all=True, append_images=images[1:], duration=times, loop=0, optimize=True)
    else:
        work = out + ".frames"
        os.makedirs(work, exist_ok=True)
        try:
            lines = []
            for i, (im, ms) in enumerate(zip(images, times)):
                path = os.path.join(work, f"{i:04d}.png")
                im.save(path)
                lines += [f"file '{os.path.abspath(path)}'", f"duration {ms / 1000:.3f}"]
            lines.append(f"file '{os.path.abspath(path)}'")             # the concat reader needs the last one twice
            with open(os.path.join(work, "list.txt"), "w") as f:
                f.write("\n".join(lines) + "\n")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
                            os.path.join(work, "list.txt"), "-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0",
                            "-pix_fmt", "yuv420p", "-vsync", "vfr", out], check=True)
        finally:
            for n in os.listdir(work):
                os.remove(os.path.join(work, n))
            os.rmdir(work)
    return len(names), len(images), sum(times), os.path.getsize(out)


def main(argv):
    args, opts = [], {"width": 0, "crop_bottom": 24, "hold_last": 1500, "override": None, "quality": 100}
    flags = {"--width": "width", "--crop-bottom": "crop_bottom", "--hold-last": "hold_last", "--ms": "override",
             "--quality": "quality"}
    i = 1
    while i < len(argv):
        if argv[i] in flags and i + 1 < len(argv) and argv[i + 1].isdigit():
            opts[flags[argv[i]]] = int(argv[i + 1])
            i += 2
        elif argv[i].startswith("--"):
            print(__doc__.strip())
            return 2
        else:
            args.append(argv[i])
            i += 1
    if len(args) != 2:
        print(__doc__.strip())
        return 2
    taken, kept, ms, size = make(args[0], args[1], **opts)
    print(f"{args[1]}: {taken} frames ({kept} different), plays for {ms / 1000:.1f} s, {size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
