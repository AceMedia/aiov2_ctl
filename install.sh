#!/bin/sh
# Bootstrap for a fresh uConsole: fetch aiov2_ctl.py and let it install everything.
#
#   sh install.sh                      install every feature (asks once before changing anything)
#   sh install.sh --dry-run            show the plan for the usual set and change nothing (no root needed)
#   sh install.sh --only core,tray     any flags are passed on to: aiov2_ctl --install
#
# Run it as yourself, not as root: it calls sudo for the one step that needs it, so the installer knows whose
# home the ops app belongs in. The only thing downloaded and run as root is this repository's own aiov2_ctl.py.
set -eu

URL="https://raw.githubusercontent.com/AceMedia/aiov2_ctl/main/aiov2_ctl.py"

die() {
    printf 'install.sh: %s\n' "$*" >&2
    exit 1
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
file="$tmp/aiov2_ctl.py"

# 3. Download, then check it is a whole Python file before anything runs it.
printf 'Downloading %s\n' "$URL"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL --proto '=https' -o "$file" "$URL" || die "the download failed."
elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$file" "$URL" || die "the download failed."
else
    die "neither curl nor wget is installed. Install one with: sudo apt install curl"
fi
python3 -m py_compile "$file" || die "the downloaded file is not valid Python; nothing was run."

# 4. Install. Everything, unless told otherwise.
[ "$#" -gt 0 ] || set -- --all
for arg in "$@"; do
    if [ "$arg" = "--dry-run" ]; then
        printf 'Showing the plan only (no root needed): python3 aiov2_ctl.py --install %s\n\n' "$*"
        python3 "$file" --install "$@"
        exit $?
    fi
done
printf 'About to run as root: sudo python3 %s --install %s\n' "$file" "$*"
printf 'It prints its plan and asks before changing anything.\n\n'
sudo python3 "$file" --install "$@"
