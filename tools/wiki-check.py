#!/usr/bin/env python3
"""Check the wiki source in docs/wiki/ before it is published.

It checks four things:

  1. every relative link names a page that exists;
  2. every link to a file (img/...) names a file that exists;
  3. every "#heading" part of a link names a heading on that page;
  4. every page is listed in _Sidebar.md.

GitHub writes some page file names with a Unicode hyphen (U+2010), for example "CLI‐Usage.md", while links
to them use a plain hyphen. The two are treated as the same character.

Links to other sites are not fetched: this script never uses the network.

Usage: python3 tools/wiki-check.py [FOLDER]      (FOLDER defaults to docs/wiki next to this script's folder)
Exit status: 0 when nothing is broken, 1 when something is, 2 when the folder is not there.
"""
import os
import re
import sys
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.dirname(HERE), "docs", "wiki")
SPECIAL = ("_Sidebar", "_Footer", "_Header")      # parts of every page, not pages of their own

_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_MD_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_HTML_LINK = re.compile(r"<(?:a|img)\b[^>]*?\b(?:href|src)\s*=\s*[\"']([^\"']+)[\"']", re.I)
_WIKI_LINK = re.compile(r"\[\[([^\]\n]+)\]\]")
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def plain_hyphens(name):
    """`name` with the Unicode hyphens GitHub puts in file names turned into plain ones."""
    return name.replace("‐", "-").replace("‑", "-")


def page_name(filename):
    """The name a link uses for the page kept in `filename`: no ".md", plain hyphens."""
    return plain_hyphens(filename[:-3] if filename.endswith(".md") else filename)


def without_code_blocks(text):
    """`text` with fenced code blocks emptied. Line numbers stay the same."""
    out, fence = [], None
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)
            out.append("")
        elif fence is not None:
            if m and m.group(1) == fence:
                fence = None
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def find_links(text):
    """[(line number, target)] for every link and picture in `text`, outside code."""
    found = []
    for n, line in enumerate(without_code_blocks(text).split("\n"), 1):
        line = _INLINE_CODE.sub("", line)
        for rx in (_MD_LINK, _HTML_LINK):
            found.extend((n, m.group(1)) for m in rx.finditer(line))
        for m in _WIKI_LINK.finditer(line):
            inner = m.group(1)
            target = inner.split("|", 1)[1] if "|" in inner else inner
            found.append((n, target.strip().replace(" ", "-")))
    return found


def anchor_for(heading):
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces turned into hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)      # a link in a heading: keep its words
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "").replace("*", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def heading_anchors(text):
    """Every anchor on a page. A heading used twice gets "-1", "-2" on the later ones, as GitHub does."""
    anchors, seen = set(), {}
    for line in without_code_blocks(text).split("\n"):
        m = _HEADING.match(line)
        if not m:
            continue
        base = anchor_for(m.group(1))
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchors.add(base if count == 0 else "%s-%d" % (base, count))
    return anchors


def split_target(target):
    """("external" | "file" | "page", path, anchor) for a link target. `anchor` is None when there is none."""
    if _SCHEME.match(target) or target.startswith("//"):
        return "external", target, None
    path, _, anchor = target.partition("#")
    path = unquote(path.split("?", 1)[0])
    anchor = unquote(anchor) if "#" in target else None
    last = path.rsplit("/", 1)[-1]
    if "/" in path or ("." in last and not last.endswith(".md")):
        return "file", path, anchor
    return "page", page_name(path), anchor


def check_pages(pages, files):
    """Every problem in a wiki, as a list of strings; empty when there is none.

    `pages` is {file name: text} for the Markdown files; `files` is the set of every other file's path
    relative to the wiki folder, with "/" between the parts.
    """
    problems = []
    names = {page_name(f): f for f in pages}
    anchors = {name: heading_anchors(pages[f]) for name, f in names.items()}
    for f in sorted(pages):
        here = page_name(f)
        for line, target in find_links(pages[f]):
            kind, path, anchor = split_target(target)
            where = "%s:%d" % (plain_hyphens(f), line)
            if kind == "external":
                continue
            if kind == "file":
                if path.startswith("/") or ".." in path.split("/"):
                    problems.append("%s: link leaves the wiki folder: %s" % (where, target))
                elif path not in files:
                    problems.append("%s: no such file: %s" % (where, path))
                continue
            page = path or here
            if page not in names:
                problems.append("%s: no such page: %s" % (where, path))
            elif anchor and anchor.lower() not in anchors[page]:
                problems.append("%s: no heading \"#%s\" on page %s" % (where, anchor, page))
    sidebar = names.get("_Sidebar")
    if sidebar is None:
        problems.append("_Sidebar.md is missing")
    else:
        listed = {path for kind, path, _ in (split_target(t) for _, t in find_links(pages[sidebar]))
                  if kind == "page"}
        for name in sorted(names):
            if name not in SPECIAL and name not in listed:
                problems.append("_Sidebar.md: page not listed: %s" % name)
    return problems


def unused_files(pages, files):
    """Files no page links to. Not a fault, only worth knowing."""
    used = set()
    for text in pages.values():
        for _, target in find_links(text):
            kind, path, _ = split_target(target)
            if kind == "file":
                used.add(path)
    return sorted(files - used)


def read_wiki(folder):
    """(pages, files) for `folder`, in the shape check_pages() takes."""
    pages, files = {}, set()
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            if root == folder and name.endswith(".md"):
                with open(full, encoding="utf-8") as fh:
                    pages[name] = fh.read()
            else:
                files.add(rel)
    return pages, files


def check_wiki(folder):
    """Every problem in the wiki kept in `folder`."""
    pages, files = read_wiki(folder)
    return check_pages(pages, files)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    folder = args[0] if args else DEFAULT_DIR
    if not os.path.isdir(folder):
        print("wiki-check: no such folder: %s" % folder, file=sys.stderr)
        return 2
    pages, files = read_wiki(folder)
    problems = check_pages(pages, files)
    for name in unused_files(pages, files):
        print("note: no page links to %s" % name)
    for problem in problems:
        print("BROKEN  %s" % problem)
    count = len([p for p in pages if page_name(p) not in SPECIAL])
    if problems:
        print("wiki-check: %d problem(s) in %d pages" % (len(problems), count))
        return 1
    print("wiki-check: %d pages and %d files, nothing broken" % (count, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
