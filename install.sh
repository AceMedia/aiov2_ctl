#!/bin/sh
# Bootstrap for a fresh uConsole: fetch this tool and let it install everything.
#
#   sh install.sh                      install every feature (asks once before changing anything)
#   sh install.sh --dry-run            show the plan for the usual set and change nothing (no root needed)
#   sh install.sh --only core,tray     any flags are passed on to: aiov2_ctl --install
#
# Run it as yourself, not as root: it calls sudo for the one step that needs it, so the installer knows whose
# home the ops app belongs in. With git it keeps a checkout of this repository (one it finds, else a new one in
# ~/aiov2_ctl-ace), which is where the tray's icons come from and what `aiov2_ctl --update` pulls. Without git
# it downloads the one program file instead. What runs as root is this repository's own aiov2_ctl.py and, when
# git is missing and you agree, apt to install git.
set -eu

# The one place this script names the repository it belongs to: owner/name on GitHub. The README's download
# line must name the same one (a test checks it).
REPO="AceMedia/aiov2_ctl"
URL="https://raw.githubusercontent.com/$REPO/main/aiov2_ctl.py"
REPO_URL="https://github.com/$REPO.git"
DIR="${AIOV2_DIR:-$HOME/aiov2_ctl-ace}"
RECORD="${AIOV2_RECORD:-/usr/local/share/aiov2_ctl/install.json}"

die() {
    printf 'install.sh: %s\n' "$*" >&2
    exit 1
}

note() {
    printf 'install.sh: %s\n' "$*"
}

# True when $1 is a git checkout of this repository (and not, say, the project this one was forked from).
ours() {
    [ -n "$1" ] && [ -f "$1/aiov2_ctl.py" ] && [ -e "$1/.git" ] || return 1
    git -C "$1" remote get-url origin 2>/dev/null | grep -Eqi "github\.com[:/]$REPO(\.git)?/?\$"
}

# 1. The right kind of system.
[ -r /etc/os-release ] || die "cannot read /etc/os-release; this is for Debian or Raspberry Pi OS."
ids=$(sed -n -e 's/^ID=//p' -e 's/^ID_LIKE=//p' /etc/os-release | tr -d '"' | tr '\n' ' ')
case " $ids " in
    *debian*|*raspbian*) ;;
    *) die "this is for Debian or Raspberry Pi OS (found: $ids)." ;;
esac
command -v python3 >/dev/null 2>&1 || die "python3 is missing. Install it with: sudo apt install python3"
[ "$(id -u)" -ne 0 ] || die "run this as yourself, not as root; it asks for sudo when it needs it."

# 2. A private folder that is removed again however this ends.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
trap 'exit 1' INT TERM

# Everything, unless told otherwise. A dry run changes nothing, so it makes no checkout either.
[ "$#" -gt 0 ] || set -- --all
dry=0
for arg in "$@"; do
    [ "$arg" != "--dry-run" ] || dry=1
done

# git is what gives a checkout. Without it, offer to install it first: --yes agrees, a terminal is asked, and
# with nobody to ask it carries on with the one file.
yes=0
for arg in "$@"; do
    case "$arg" in --yes|-y) yes=1 ;; esac
done
if [ "$dry" -eq 0 ] && ! command -v git >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    note "git is not installed. With it this tool keeps a checkout: the tray's icons come from it and --update pulls it."
    go=$yes
    if [ "$go" -eq 0 ] && [ -t 0 ]; then
        printf 'Install git now? [Y/n]: '
        read -r answer || answer=n
        case "$answer" in ""|y|Y|yes|Yes) go=1 ;; esac
    fi
    if [ "$go" -eq 1 ]; then
        printf 'About to run as root: sudo apt-get update, then sudo apt-get install -y git\n'
        if sudo apt-get update -q && sudo apt-get install -y -q git; then
            note "git is installed."
        else
            note "git could not be installed; carrying on without a checkout."
        fi
    else
        note "carrying on without git. Install it (sudo apt install git) and run this again to get a checkout."
    fi
fi

# 3. Find or make a checkout: the one an earlier install recorded, else the usual folder, else ~/aiov2_ctl (where
# the README has always said to clone it), else a new clone. Each is used only if it is this repository.
checkout=""
if command -v git >/dev/null 2>&1; then
    recorded=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("repo_path") or "")' "$RECORD" 2>/dev/null || true)
    for candidate in "$recorded" "$DIR" "$HOME/aiov2_ctl"; do
        if ours "$candidate"; then
            checkout=$candidate
            break
        fi
    done
    if [ -n "$checkout" ]; then
        if [ "$dry" -eq 1 ]; then
            note "using the checkout in $checkout as it is."
        elif [ -n "$(git -C "$checkout" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
            note "$checkout has changes of your own, so it was not updated; installing what is there."
        elif GIT_TERMINAL_PROMPT=0 git -C "$checkout" pull --ff-only >/dev/null 2>&1; then
            note "updated the checkout in $checkout."
        else
            note "$checkout could not be updated; installing what is there."
        fi
    elif [ "$dry" -eq 1 ]; then
        :
    elif [ -e "$DIR" ]; then
        note "$DIR is there but is not a checkout of this tool, so it was left alone."
    else
        printf 'Cloning %s into %s\n' "$REPO_URL" "$DIR"
        if GIT_TERMINAL_PROMPT=0 git clone --quiet "$REPO_URL" "$DIR"; then
            checkout=$DIR
        else
            note "the clone failed; downloading the one program file instead."
        fi
    fi
fi

# 4. Without a checkout, download the one file. The tray then uses a stock icon and --update has nothing to pull.
if [ -n "$checkout" ]; then
    file="$checkout/aiov2_ctl.py"
else
    file="$tmp/aiov2_ctl.py"
    printf 'Downloading %s\n' "$URL"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --proto '=https' -o "$file" "$URL" || die "the download failed."
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$file" "$URL" || die "the download failed."
    else
        die "neither curl nor wget is installed. Install one with: sudo apt install curl"
    fi
    [ "$dry" -eq 1 ] || note "no checkout was made, so the tray uses a stock icon and --update has nothing to pull."
fi

# 5. Check it is a whole Python file before anything runs it. The compiled copy goes in the private folder.
python3 -c 'import py_compile,sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)' "$file" "$tmp/check.pyc" \
    || die "$file is not valid Python; nothing was run."

# 6. Install.
if [ "$dry" -eq 1 ]; then
    printf 'Showing the plan only (no root needed): python3 aiov2_ctl.py --install %s\n\n' "$*"
    python3 "$file" --install "$@"
    exit $?
fi
printf 'About to run as root: sudo python3 %s --install %s\n' "$file" "$*"
printf 'It prints its plan and asks before changing anything.\n\n'
sudo python3 "$file" --install "$@"
