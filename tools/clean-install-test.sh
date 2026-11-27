#!/bin/sh
# A clean-install test: build a throwaway clean Debian system in a folder and run a real install in it.
#
#   sh tools/clean-install-test.sh                       Debian 13 (trixie), this checkout's current commit
#   sh tools/clean-install-test.sh --suite bookworm      Debian 12
#   sh tools/clean-install-test.sh --ref beta            a branch, tag or commit instead of the current commit
#   sh tools/clean-install-test.sh --keep-logs           keep the logs even when everything passes
#
# It needs mmdebstrap, git, unshare and sudo, and a network connection (it downloads Debian packages). Run it
# as yourself, not as root: it calls sudo for the steps that need it. It takes ten minutes or so.
#
# What it does: makes a minimal Debian in a new folder under /var/tmp, adds a user called tester, and then, as
# that user inside that system, installs this tool twice over (once from one loose file, once from a checkout),
# starts the ops app without a screen, runs the tests, installs again and uninstalls. Every step is checked and
# prints PASS or FAIL. The folder is removed at the end, also on a failure or Ctrl+C. Uncommitted changes are
# not tested: the copy that goes in is a clean clone of the commit.
#
# The install runs inside a chroot with its own mount and process namespaces, so it never writes to this
# machine's own install, and nothing it mounts is visible outside. Services are not started in it. On plain
# Debian meshtasticd is not in the package sources (nor readsb on Debian 12), so those features come out as
# "not installed"; that is right and is not counted as a failure.
#
# Exit code: 0 every check passed, 1 a check failed or the run did not finish, 2 bad arguments or a refusal.
set -u

SUITE=trixie
REF=HEAD
KEEP_LOGS=0

usage() {
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
    printf 'clean-install-test: %s\n' "$*" >&2
    exit 2
}

note() {
    printf 'clean-install-test: %s\n' "$*"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --suite) [ $# -ge 2 ] || die "--suite needs a name: bookworm or trixie"; SUITE=$2; shift 2 ;;
        --suite=*) SUITE=${1#--suite=}; shift ;;
        --ref) [ $# -ge 2 ] || die "--ref needs a branch, tag or commit"; REF=$2; shift 2 ;;
        --ref=*) REF=${1#--ref=}; shift ;;
        --keep-logs) KEEP_LOGS=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done
case "$SUITE" in
    bookworm|trixie) ;;
    *) die "--suite must be bookworm or trixie (got: $SUITE)" ;;
esac

[ "$(id -u)" -ne 0 ] || die "run this as yourself, not as root; it calls sudo for the steps that need it."
for tool in mmdebstrap git unshare sudo chroot; do
    command -v "$tool" >/dev/null 2>&1 || PATH="$PATH:/usr/sbin:/sbin" command -v "$tool" >/dev/null 2>&1 \
        || die "$tool is missing. Install it with: sudo apt install mmdebstrap git util-linux sudo"
done

# The checkout this script is in, and the commit to test.
SRC=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || die "cannot find the checkout this script is in."
git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1 || die "$SRC is not a git checkout."
COMMIT=$(git -C "$SRC" rev-parse --verify -q "$REF^{commit}" 2>/dev/null) \
    || COMMIT=$(git -C "$SRC" rev-parse --verify -q "origin/$REF^{commit}" 2>/dev/null) \
    || die "$REF is not a branch, tag or commit in $SRC (try: git -C $SRC fetch)."
# The clone inside is put on a branch, as a user's would be: the one named, else the one this checkout is on.
BRANCH=""
if git -C "$SRC" show-ref --verify -q "refs/heads/$REF" || git -C "$SRC" show-ref --verify -q "refs/remotes/origin/$REF"; then
    BRANCH=$REF
elif [ "$REF" = HEAD ]; then
    BRANCH=$(git -C "$SRC" symbolic-ref -q --short HEAD 2>/dev/null || true)
fi
[ -n "$BRANCH" ] || BRANCH=clean-install-test

# A new folder under /var/tmp with a name nobody else has. It is never reused.
NAME="aiov2-clean-$SUITE-$(date +%Y%m%d-%H%M%S)-$$"
ROOT="/var/tmp/$NAME"
WORK="/var/tmp/$NAME-work"
LOGS="/var/tmp/$NAME-logs"
for path in "$ROOT" "$WORK" "$LOGS"; do
    [ ! -e "$path" ] && [ ! -L "$path" ] || die "$path is already there; nothing was done."
done

sudo true || die "sudo is needed for mmdebstrap, chroot and the clean-up."

FINISHED=0
CLEANED=0

# True when anything at or under the folder is still mounted here.
has_mounts() {
    awk -v root="$ROOT" '$2 == root || index($2, root "/") == 1 { found = 1 } END { exit !found }' /proc/mounts
}

cleanup() {
    [ "$CLEANED" -eq 0 ] || return 0
    CLEANED=1
    rm -rf "$WORK"
    if [ -e "$ROOT" ]; then
        if has_mounts; then
            printf 'clean-install-test: NOT removing %s: something under it is still mounted:\n' "$ROOT" >&2
            awk -v root="$ROOT" '$2 == root || index($2, root "/") == 1 { print "  " $2 }' /proc/mounts >&2
            printf 'Unmount those, then: sudo rm -rf --one-file-system %s\n' "$ROOT" >&2
        elif sudo rm -rf --one-file-system "$ROOT"; then
            note "removed $ROOT"
        else
            printf 'clean-install-test: could not remove %s\n' "$ROOT" >&2
        fi
    fi
}
trap cleanup EXIT
trap 'printf "\nclean-install-test: interrupted\n" >&2; exit 130' INT
trap 'exit 143' TERM HUP

save_logs() {
    [ -d "$ROOT/home/tester/logs" ] || return 0
    mkdir -p "$LOGS"
    sudo cp -r "$ROOT/home/tester/logs/." "$LOGS/" 2>/dev/null
    [ ! -f "$WORK/mmdebstrap.log" ] || cp "$WORK/mmdebstrap.log" "$LOGS/"
    sudo chown -R "$(id -u):$(id -g)" "$LOGS"
    note "the logs are in $LOGS"
}

fail_setup() {
    printf 'clean-install-test: %s\n' "$*" >&2
    exit 1
}

note "testing commit $(git -C "$SRC" rev-parse --short "$COMMIT") (branch $BRANCH) on Debian $SUITE in $ROOT"
mkdir "$WORK" || die "cannot make $WORK"

# 1. A clean clone of the commit, and the one program file on its own.
git clone -q --no-checkout "$SRC" "$WORK/aiov2_ctl" || fail_setup "could not clone $SRC"
if ! git -C "$WORK/aiov2_ctl" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
    git -C "$WORK/aiov2_ctl" fetch -q "$SRC" "$REF" 2>/dev/null
    git -C "$WORK/aiov2_ctl" cat-file -e "$COMMIT^{commit}" 2>/dev/null \
        || fail_setup "the clone does not hold $COMMIT; make a local branch of it first."
fi
git -C "$WORK/aiov2_ctl" checkout -q -B "$BRANCH" "$COMMIT" || fail_setup "could not check out $COMMIT"
origin=$(git -C "$SRC" remote get-url origin 2>/dev/null || true)
if [ -n "$origin" ]; then
    git -C "$WORK/aiov2_ctl" remote set-url origin "$origin"
else
    git -C "$WORK/aiov2_ctl" remote remove origin
fi
mkdir "$WORK/dl"
git -C "$SRC" show "$COMMIT:aiov2_ctl.py" > "$WORK/dl/aiov2_ctl.py" || fail_setup "the commit has no aiov2_ctl.py"

# 2. The script that runs inside, as tester. It records one line per check in ~/results.txt.
cat > "$WORK/inner.sh" <<'INNER_EOF'
#!/bin/sh
# Runs inside the clean system as the user tester. Written by tools/clean-install-test.sh.
cd "$HOME" || exit 1
CHECKOUT="$HOME/aiov2_ctl"
APPS="$HOME/.local/share/applications"
mkdir -p logs
: > results.txt
TAB=$(printf '\t')

result() {      # result PASS|FAIL id "what was checked" "what was seen"
    printf '%s  %-3s %s%s\n' "$1" "$2" "$3" "${4:+  ($4)}"
    printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4:-}" >> results.txt
}

check() {       # check id "what" "what was seen" command...: PASS when the command succeeds
    _id=$1; _what=$2; _seen=$3; shift 3
    if "$@"; then result PASS "$_id" "$_what" "$_seen"; else result FAIL "$_id" "$_what" "$_seen"; fi
}

row() {         # row LOG FEATURE: that feature's line from the last Result table in an install log
    awk -v key="$2" '/^Result$/ { on = 1; line = ""; next } on && $1 == key { line = $0 } END { print line }' "$1" \
        | sed 's/^ *//' | cut -c1-100
}

recorded() {    # recorded FEATURE: "True" or "False", as aiov2_ctl --features --json has it
    aiov2_ctl --features --json 2>/dev/null | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)["features"]
except Exception:
    print("unreadable"); sys.exit()
print(next((str(r["installed"]) for r in rows if r["key"] == sys.argv[1]), "missing"))' "$1"
}

not_installed() {   # the install said "not installed" for it and the record agrees
    case "$(row "$1" "$2")" in "$2"*" not installed:"*) ;; *) return 1 ;; esac
    [ "$(recorded "$2")" = False ]
}

installed() {       # the install said "installed" (or "already there") and the record agrees
    case "$(row "$1" "$2")" in "$2"*" installed"*|"$2"*" already there"*) ;; *) return 1 ;; esac
    case "$(row "$1" "$2")" in *"not installed"*) return 1 ;; esac
    [ "$(recorded "$2")" = True ]
}

show_rows() {
    awk '/^Result$/ { on = 1 } on' "$1" | cut -c1-160 | sed 's/^/    | /'
}

echo
echo "== a. one downloaded file, no checkout: sudo python3 aiov2_ctl.py --install --all --yes"
sudo python3 "$HOME/dl/aiov2_ctl.py" --install --all --yes > logs/a-one-file.log 2>&1 < /dev/null
rc=$?
show_rows logs/a-one-file.log
check a1 "one file: the install exits 0" "exit $rc" [ "$rc" -eq 0 ]
check a2 "one file: the app is not installed" "$(row logs/a-one-file.log app | cut -c1-40)" not_installed logs/a-one-file.log app
check a3 "one file: the console is not installed" "$(row logs/a-one-file.log console | cut -c1-40)" not_installed logs/a-one-file.log console
echo "    mesh: $(row logs/a-one-file.log mesh | cut -c1-70)"
echo "    adsb: $(row logs/a-one-file.log adsb | cut -c1-70)"

echo
echo "== b. from a checkout in $CHECKOUT: sudo python3 aiov2_ctl.py --install --all --yes"
if ! command -v git >/dev/null 2>&1; then
    # Somebody with a checkout has git; the one-file install above does not bring it.
    echo "    git is not here yet; installing it as a user with a checkout would have"
    sudo apt-get install -y -q git > logs/b-git.log 2>&1 < /dev/null
fi
(cd "$CHECKOUT" && sudo python3 aiov2_ctl.py --install --all --yes) > logs/b-checkout.log 2>&1 < /dev/null
rc=$?
show_rows logs/b-checkout.log
check b1 "checkout: the install exits 0" "exit $rc" [ "$rc" -eq 0 ]
check b2 "checkout: the app is installed" "$(row logs/b-checkout.log app | cut -c1-40)" installed logs/b-checkout.log app
check b3 "checkout: the console is installed" "$(row logs/b-checkout.log console | cut -c1-40)" installed logs/b-checkout.log console
paths=$(grep -h '^Path=' "$APPS/uconsole-ops-gui.desktop" "$APPS/uconsole-ops-console.desktop" 2>&1 | sort -u | tr '\n' ' ')
shortcuts_point_at_checkout() {
    [ -f "$APPS/uconsole-ops-gui.desktop" ] && [ -f "$APPS/uconsole-ops-console.desktop" ] \
        && [ "$paths" = "Path=$CHECKOUT " ]
}
check b4 "checkout: both shortcuts point at the checkout" "$paths" shortcuts_point_at_checkout
no_old_clone() { [ ! -e "$HOME/ops-agentic" ] && [ ! -L "$HOME/ops-agentic" ]; }
check b5 "checkout: ~/ops-agentic was not made" "" no_old_clone
echo "    mesh: $(row logs/b-checkout.log mesh | cut -c1-70)"
echo "    adsb: $(row logs/b-checkout.log adsb | cut -c1-70)"

echo
echo "== c. the app starts: QT_QPA_PLATFORM=offscreen timeout 15 python3 -m ops_gui"
(cd "$CHECKOUT" && QT_QPA_PLATFORM=offscreen timeout 15 python3 -m ops_gui) > logs/c-app.log 2>&1 < /dev/null
rc=$?
app_ran() { [ "$rc" -eq 124 ] && ! grep -q 'Traceback' logs/c-app.log; }
seen="exit $rc"
if grep -q 'Traceback' logs/c-app.log; then
    seen="$seen, traceback: $(grep -v '^ ' logs/c-app.log | tail -1 | cut -c1-80)"
    tail -20 logs/c-app.log | sed 's/^/    | /'
fi
check c1 "the app runs for 15 seconds with no traceback" "$seen" app_ran

echo
echo "== d. the console answers: python3 ops.py --help"
(cd "$CHECKOUT" && python3 ops.py --help) > logs/d-console.log 2>&1 < /dev/null
rc=$?
[ "$rc" -eq 0 ] || tail -20 logs/d-console.log | sed 's/^/    | /'
check d1 "ops.py --help exits 0" "exit $rc" [ "$rc" -eq 0 ]

echo
echo "== e. the tests: python3 -m unittest discover -s tests"
(cd "$CHECKOUT" && python3 -m unittest discover -s tests) > logs/e-tests.log 2>&1 < /dev/null
rc=$?
ran=$(grep -E '^Ran [0-9]+ tests' logs/e-tests.log | tail -1 | cut -d' ' -f1-3)
verdict=$(grep -E '^(OK|FAILED)' logs/e-tests.log | tail -1)
[ "$rc" -eq 0 ] || grep -E '^(ERROR|FAIL):' logs/e-tests.log | head -20 | sed 's/^/    | /'
check e1 "the tests pass" "${ran:-no count}: ${verdict:-no verdict}, exit $rc" [ "$rc" -eq 0 ]

echo
echo "== f. a second install: sudo aiov2_ctl --install --yes"
sudo aiov2_ctl --install --yes > logs/f-again.log 2>&1 < /dev/null
rc=$?
show_rows logs/f-again.log
check f1 "a second install exits 0" "exit $rc" [ "$rc" -eq 0 ]

echo
echo "== g. uninstall: sudo aiov2_ctl --uninstall --yes"
sudo aiov2_ctl --uninstall --yes > logs/g-uninstall.log 2>&1 < /dev/null
rc=$?
show_rows logs/g-uninstall.log
check g1 "uninstall exits 0" "exit $rc" [ "$rc" -eq 0 ]
program_gone() { [ ! -e /usr/local/bin/aiov2_ctl ] && [ ! -L /usr/local/bin/aiov2_ctl ]; }
check g2 "uninstall: the program is gone" "" program_gone
left=$(ls "$APPS"/uconsole-ops-*.desktop /usr/share/applications/aiov2_ctl*.desktop \
          "$HOME/.config/autostart/aiov2_ctl.desktop" 2>/dev/null | tr '\n' ' ')
check g3 "uninstall: the shortcuts are gone" "${left:+left: $left}" [ -z "$left" ]

echo DONE >> results.txt
INNER_EOF

# 3. The clean system.
note "building a clean Debian $SUITE with mmdebstrap (a few minutes)"
if ! sudo mmdebstrap --variant=important --include=sudo,curl,ca-certificates,python3 "$SUITE" "$ROOT" \
        > "$WORK/mmdebstrap.log" 2>&1; then
    tail -20 "$WORK/mmdebstrap.log" >&2
    fail_setup "mmdebstrap failed."
fi
sudo chroot "$ROOT" useradd -m -u 1000 -s /bin/bash tester || fail_setup "could not add the user tester."
echo 'tester ALL=(ALL) NOPASSWD:ALL' | sudo tee "$ROOT/etc/sudoers.d/tester" >/dev/null
sudo chmod 440 "$ROOT/etc/sudoers.d/tester"
# No service is started inside: this is a folder, not a running system.
printf '#!/bin/sh\nexit 101\n' | sudo tee "$ROOT/usr/sbin/policy-rc.d" >/dev/null
sudo chmod 755 "$ROOT/usr/sbin/policy-rc.d"
sudo cp /etc/resolv.conf "$ROOT/etc/resolv.conf"
echo "127.0.0.1 localhost $(hostname)" | sudo tee "$ROOT/etc/hosts" >/dev/null
sudo cp -r "$WORK/aiov2_ctl" "$WORK/dl" "$WORK/inner.sh" "$ROOT/home/tester/" || fail_setup "could not copy the test in."
sudo chroot "$ROOT" chown -R tester:tester /home/tester || fail_setup "could not hand the files to tester."

# 4. Run it. The mounts are made in a mount namespace of their own, so none of them exists outside it, and the
# process namespace takes everything started inside down with it when the run ends.
note "running the install test inside it"
sudo unshare --mount --propagation private --pid --fork --kill-child sh -c "
    mount -t proc proc '$ROOT/proc' &&
    mount -o ro -t sysfs sys '$ROOT/sys' &&
    mount --rbind /dev '$ROOT/dev' &&
    mount -t tmpfs tmpfs '$ROOT/run' &&
    mount -t tmpfs tmpfs '$ROOT/tmp' &&
    chroot '$ROOT' su - tester -c 'sh ~/inner.sh'" < /dev/null
inner_rc=$?
FINISHED=1

# 5. The summary.
RESULTS="$WORK/results.txt"
sudo cat "$ROOT/home/tester/results.txt" > "$RESULTS" 2>/dev/null || : > "$RESULTS"
passed=$(grep -c '^PASS' "$RESULTS")
failed=$(grep -c '^FAIL' "$RESULTS")
complete=0
grep -q '^DONE$' "$RESULTS" && complete=1

printf '\nSummary: Debian %s, commit %s\n' "$SUITE" "$(git -C "$SRC" rev-parse --short "$COMMIT")"
printf '%s\n' "----------------------------------------------------------------------------------------------"
awk -F '\t' '$1 == "PASS" || $1 == "FAIL" { printf "%-4s  %-3s %-48s %s\n", $1, $2, $3, $4 }' "$RESULTS" | cut -c1-140
printf '%s\n' "----------------------------------------------------------------------------------------------"

status=0
if [ "$complete" -ne 1 ]; then
    printf 'NOT FINISHED: the run inside stopped early (exit %s); %s passed, %s failed before that.\n' "$inner_rc" "$passed" "$failed"
    status=1
elif [ "$failed" -ne 0 ]; then
    printf 'FAILED: %s of %s checks failed.\n' "$failed" "$((passed + failed))"
    status=1
else
    printf 'PASSED: all %s checks.\n' "$passed"
fi
if [ "$status" -ne 0 ] || [ "$KEEP_LOGS" -eq 1 ]; then
    save_logs
fi
exit "$status"

