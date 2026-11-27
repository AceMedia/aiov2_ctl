#!/usr/bin/env bash
# Publish docs/wiki/ to the GitHub wiki of this checkout's repository.
#
#   tools/wiki-publish.sh --dry-run        show what would change, change nothing
#   tools/wiki-publish.sh                  the same: show the changes and stop
#   tools/wiki-publish.sh --yes            show the changes, then commit and push them
#   tools/wiki-publish.sh --repo OWNER/REPO ...    publish to that repository's wiki instead of origin's
#
# What it does: runs tools/wiki-check.py and stops if it finds a fault; clones the wiki into a temporary
# folder; copies in the pages and pictures that are committed under docs/wiki/; shows `git status --short`;
# and, only with --yes, commits and pushes. It never force-pushes. It never deletes a page or picture that is
# only in the wiki: it names each one and leaves it alone.
#
# Only committed files are published. A file that is in docs/wiki/ but not committed stays behind.
set -euo pipefail

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

REPO="" YES=0 DRY=0 WIKI_URL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --repo) [ $# -ge 2 ] || { echo "wiki-publish: --repo needs OWNER/REPO" >&2; exit 2; }; REPO=$2; shift 2 ;;
        --repo=*) REPO=${1#--repo=}; shift ;;
        # For testing against a local repository. Not needed for GitHub.
        --wiki-url) [ $# -ge 2 ] || { echo "wiki-publish: --wiki-url needs an address" >&2; exit 2; }; WIKI_URL=$2; shift 2 ;;
        --yes) YES=1; shift ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "wiki-publish: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
SRC=docs/wiki

# OWNER/REPO from an origin address: https://github.com/O/R(.git), git@github.com:O/R(.git), ssh://git@github.com/O/R
owner_repo() {
    local url=${1%/}
    url=${url%.git}
    case "$url" in
        *://*) url=${url#*://}; url=${url#*/} ;;
        *@*:*) url=${url#*:} ;;
        *) return 1 ;;
    esac
    case "$url" in
        */*/*|/*|*/) return 1 ;;
        */*) printf '%s\n' "$url" ;;
        *) return 1 ;;
    esac
}

if [ -z "$WIKI_URL" ]; then
    if [ -z "$REPO" ]; then
        ORIGIN=$(git -C "$ROOT" remote get-url origin) || { echo "wiki-publish: this checkout has no origin; use --repo OWNER/REPO" >&2; exit 2; }
        REPO=$(owner_repo "$ORIGIN") || { echo "wiki-publish: cannot read OWNER/REPO from origin ($ORIGIN); use --repo OWNER/REPO" >&2; exit 2; }
    fi
    case "$REPO" in
        */*/*|/*|*/|*' '*) echo "wiki-publish: --repo must be OWNER/REPO, got: $REPO" >&2; exit 2 ;;
        */*) ;;
        *) echo "wiki-publish: --repo must be OWNER/REPO, got: $REPO" >&2; exit 2 ;;
    esac
    WIKI_URL="https://github.com/$REPO.wiki.git"
fi
echo "Wiki: $WIKI_URL"

# 1. The checker must pass.
if ! python3 "$ROOT/tools/wiki-check.py" "$ROOT/$SRC"; then
    echo "wiki-publish: the checker found faults. Nothing was published." >&2
    exit 1
fi

# 2. Only committed files go out.
LEFT=$(git -C "$ROOT" -c core.quotepath=off status --short -- "$SRC")
if [ -n "$LEFT" ]; then
    echo "Not committed under $SRC, so NOT published:"
    printf '%s\n' "$LEFT" | sed 's/^/    /'
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Signed-in gh supplies the password for https, when it is installed.
CRED=()
if command -v gh >/dev/null 2>&1; then CRED=(-c "credential.helper=!gh auth git-credential"); fi

# 3. Clone the wiki and lay the source over it.
git ${CRED[@]+"${CRED[@]}"} clone -q "$WIKI_URL" "$WORK/wiki"
W="$WORK/wiki"
git -C "$W" config core.quotepath off

git -C "$ROOT" archive --format=tar "HEAD:$SRC" | tar -x -C "$W"

# 4. Files that are only in the wiki are named and kept.
git -C "$ROOT" ls-tree -r -z --name-only "HEAD:$SRC" | tr '\0' '\n' | sort > "$WORK/source.lst"
git -C "$W" ls-files -z | tr '\0' '\n' | sort > "$WORK/wiki.lst"
ONLY=$(comm -13 "$WORK/source.lst" "$WORK/wiki.lst")
if [ -n "$ONLY" ]; then
    echo "Only in the wiki, not in $SRC. Left alone, NOT deleted:"
    printf '%s\n' "$ONLY" | sed 's/^/    /'
    echo "    (delete them in the wiki by hand, or add them to $SRC, if that is wrong)"
fi

# 5. Show what would change.
git -C "$W" add -A
CHANGES=$(git -C "$W" status --short)
if [ -z "$CHANGES" ]; then
    echo "Nothing to change: the wiki already matches $SRC."
    exit 0
fi
echo "Changes (git status --short):"
printf '%s\n' "$CHANGES" | sed 's/^/    /'

if [ "$DRY" -eq 1 ]; then
    echo "Dry run: nothing was published."
    exit 0
fi
if [ "$YES" -ne 1 ]; then
    echo "Stopped: nothing was published. Run again with --yes to publish these changes."
    exit 3
fi

# 6. Commit and push. A plain push: if the wiki moved on meanwhile it is refused, and that is right.
NAME=$(git -C "$ROOT" config user.name || true)
MAIL=$(git -C "$ROOT" config user.email || true)
ID=()
[ -n "$NAME" ] && ID+=(-c "user.name=$NAME")
[ -n "$MAIL" ] && ID+=(-c "user.email=$MAIL")
git -C "$W" ${ID[@]+"${ID[@]}"} commit -q -m "Publish docs/wiki from $(git -C "$ROOT" rev-parse --short HEAD)"
git -C "$W" ${CRED[@]+"${CRED[@]}"} push -q origin HEAD
echo "Published to $WIKI_URL"
