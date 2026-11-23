# Reply to PR author (`fix: SDR Marked Enabled on boot #4`)

Thanks for taking a swing at this. I wanted to follow up with what I implemented and why I hard-reset/reworked the approach before merging the final fix.

## Why I hard-reset and reworked this

I hard-reset because the merged approach relied on a global inference (`no pu` => ON), and that turned out to be brittle:

- It was pin/output-format dependent.
- It could produce false positives on other rails.
- It tied behavior to spacing/format in `pinctrl` output rather than explicit state.

The issue wasn’t the observation itself; it was applying that observation as a global rule. I reworked it into an explicit per-pin state model and validated it on-device.

## What I implemented

### 1) Truth-first pin state detection

State is now derived from actual level whenever available:

- `hi` => ON
- `lo` => OFF

Only when level is `--` (not driven) do we use explicit per-pin boot defaults.

### 2) Per-pin fallback model

Fallback behavior is now explicit and local via `BOOT_DEFAULTS` (per pin), without global heuristics.

### 3) Parser hardening

We now parse level tokens robustly (`hi`, `lo`, `--`) without relying on whitespace quirks.

### 4) Backward compatibility

`GpioController.get_gpio(pin)` still returns `bool`, so existing CLI/GUI behavior remains compatible.

### 5) Extended feature work (beyond issue #4)

I also added proper rail boot control so users can actually configure rails at boot:

- CLI:
  - `aiov2_ctl --boot-rail <FEATURE> on|off|status`
  - `aiov2_ctl --boot-rails-status`
- Boot apply service:
  - `aiov2-rails-boot.service` installed/enabled via `--install`
  - applies saved boot rail settings at startup
- GUI:
  - `Rails on boot` submenu for persisted boot preferences
  - fixed config scope mismatch so GUI + service use the same effective system-backed config

## Validation and test coverage

I iterated this on device and verified all rails/pins repeatedly:

- Runtime toggles: GPS/LORA/SDR/USB all validated
  - ON path => `hi/pinctrl`
  - OFF path => `lo/pinctrl`
- Boot path validated after reboot
  - `--` path => `boot_default`
- Combined suite passed with:
  - `PASS: 24`
  - `WARN: 0`
  - `FAIL: 0`

This resolves #4 and keeps state handling explicit and testable across boot/runtime paths.

---

## Self-test script used during validation

```bash
#!/usr/bin/env bash
set -u

CTL_CMD="${AIOV2_CTL_CMD:-aiov2_ctl}"
SLEEP_ON="${AIOV2_GPIO_SLEEP_ON:-1.2}"
SLEEP_OFF="${AIOV2_GPIO_SLEEP_OFF:-1.0}"

FEATURES=("GPS" "LORA" "SDR" "USB")
PINS=(27 16 7 23)

# Keep these in sync with aiov2_ctl.py BOOT_DEFAULTS.
declare -A BOOT_DEFAULTS=(
  [7]="ON"
  [16]="OFF"
  [23]="OFF"
  [27]="OFF"
)

PASS=0
FAIL=0
WARN=0

note() { printf "%s\n" "$*"; }
pass() { PASS=$((PASS + 1)); printf "PASS: %s\n" "$*"; }
fail() { FAIL=$((FAIL + 1)); printf "FAIL: %s\n" "$*"; }
warn() { WARN=$((WARN + 1)); printf "WARN: %s\n" "$*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1"
    exit 1
  }
}

parse_level() {
  local raw="$1"

  if [[ "$raw" =~ (^|[^[:alnum:]_])hi([^[:alnum:]_]|$) ]]; then
    echo "hi"
    return
  fi

  if [[ "$raw" =~ (^|[^[:alnum:]_])lo([^[:alnum:]_]|$) ]]; then
    echo "lo"
    return
  fi

  if [[ "$raw" =~ (^|[[:space:]])--([[:space:]]|$) ]]; then
    echo "--"
    return
  fi

  echo "none"
}

derive_state_source() {
  local pin="$1"
  local raw="$2"
  local level
  level="$(parse_level "$raw")"

  if [[ "$level" == "hi" ]]; then
    echo "ON pinctrl hi"
    return
  fi

  if [[ "$level" == "lo" ]]; then
    echo "OFF pinctrl lo"
    return
  fi

  if [[ "$level" == "--" ]]; then
    local fallback="${BOOT_DEFAULTS[$pin]:-OFF}"
    echo "$fallback boot_default --"
    return
  fi

  echo "OFF unknown none"
}

collect_pin() {
  local pin="$1"
  local raw
  raw="$(pinctrl get "$pin" 2>&1 || true)"
  local derived
  derived="$(derive_state_source "$pin" "$raw")"
  local state source level
  state="$(awk '{print $1}' <<<"$derived")"
  source="$(awk '{print $2}' <<<"$derived")"
  level="$(awk '{print $3}' <<<"$derived")"

  printf "%s\t%s\t%s\t%s\n" "$state" "$source" "$level" "$raw"
}

status_line_for_feature() {
  local feature="$1"
  AIOV2_CTL_DEBUG=1 "$CTL_CMD" --status 2>/dev/null | grep -E "^${feature}[[:space:]]+GPIO" || true
}

status_extract_state() {
  local line="$1"
  grep -oE '\bON\b|\bOFF\b' <<<"$line" | head -n1
}

status_extract_source() {
  local line="$1"
  sed -n 's/.*(\([^)]*\)).*/\1/p' <<<"$line"
}

print_header() {
  echo
  echo "=============================================="
  echo "$1"
  echo "=============================================="
}

check_status_consistency() {
  local feature="$1"
  local expected_state="$2"
  local expected_source="$3"

  local line
  line="$(status_line_for_feature "$feature")"
  if [[ -z "$line" ]]; then
    warn "${feature}: could not parse '$CTL_CMD --status' output"
    return
  fi

  local got_state got_source
  got_state="$(status_extract_state "$line")"
  got_source="$(status_extract_source "$line")"

  if [[ "$got_state" == "$expected_state" ]]; then
    pass "${feature}: status state matches (${got_state})"
  else
    fail "${feature}: status state mismatch (expected ${expected_state}, got ${got_state:-n/a})"
  fi

  if [[ -n "$got_source" ]]; then
    if [[ "$got_source" == "$expected_source" ]]; then
      pass "${feature}: status source matches (${got_source})"
    else
      fail "${feature}: status source mismatch (expected ${expected_source}, got ${got_source})"
    fi
  else
    warn "${feature}: source label missing (run with AIOV2_CTL_DEBUG=1 support)"
  fi
}

boot_snapshot() {
  print_header "Boot Snapshot"
  note "Run this section immediately after reboot for best signal."
  printf "%-5s %-6s %-12s %-6s %s\n" "Feat" "Pin" "State" "Lvl" "Raw pinctrl"

  local i
  for i in "${!FEATURES[@]}"; do
    local feature pin info state source level raw
    feature="${FEATURES[$i]}"
    pin="${PINS[$i]}"
    info="$(collect_pin "$pin")"
    state="$(cut -f1 <<<"$info")"
    source="$(cut -f2 <<<"$info")"
    level="$(cut -f3 <<<"$info")"
    raw="$(cut -f4- <<<"$info")"

    printf "%-5s %-6s %-12s %-6s %s\n" "$feature" "GPIO$pin" "$state($source)" "$level" "$raw"

    if [[ "$source" == "unknown" ]]; then
      warn "${feature}/GPIO${pin}: pinctrl output unparsed"
    fi

    check_status_consistency "$feature" "$state" "$source"
  done
}

toggle_runtime_test() {
  print_header "Runtime Toggle Test"

  local i
  for i in "${!FEATURES[@]}"; do
    local feature pin info state source level raw
    feature="${FEATURES[$i]}"
    pin="${PINS[$i]}"

    note "Testing ${feature} (GPIO${pin})"

    "$CTL_CMD" "$feature" on >/dev/null 2>&1 || warn "${feature}: command failed: on"
    sleep "$SLEEP_ON"
    info="$(collect_pin "$pin")"
    state="$(cut -f1 <<<"$info")"
    source="$(cut -f2 <<<"$info")"
    level="$(cut -f3 <<<"$info")"
    raw="$(cut -f4- <<<"$info")"
    if [[ "$level" == "hi" && "$state" == "ON" && "$source" == "pinctrl" ]]; then
      pass "${feature}: ON => hi/pinctrl"
    else
      fail "${feature}: ON expected hi/pinctrl, got state=${state} source=${source} level=${level} raw='${raw}'"
    fi

    "$CTL_CMD" "$feature" off >/dev/null 2>&1 || warn "${feature}: command failed: off"
    sleep "$SLEEP_OFF"
    info="$(collect_pin "$pin")"
    state="$(cut -f1 <<<"$info")"
    source="$(cut -f2 <<<"$info")"
    level="$(cut -f3 <<<"$info")"
    raw="$(cut -f4- <<<"$info")"
    if [[ "$level" == "lo" && "$state" == "OFF" && "$source" == "pinctrl" ]]; then
      pass "${feature}: OFF => lo/pinctrl"
    else
      fail "${feature}: OFF expected lo/pinctrl, got state=${state} source=${source} level=${level} raw='${raw}'"
    fi

    check_status_consistency "$feature" "$state" "$source"
    echo
  done
}

summary() {
  print_header "Summary"
  echo "PASS: $PASS"
  echo "WARN: $WARN"
  echo "FAIL: $FAIL"
  echo

  if (( FAIL > 0 )); then
    echo "Result: FAILED"
    exit 1
  fi

  echo "Result: PASSED"
  exit 0
}

main() {
  require_cmd "$CTL_CMD"
  require_cmd "pinctrl"

  case "${1:-all}" in
    boot)
      boot_snapshot
      summary
      ;;
    runtime)
      toggle_runtime_test
      summary
      ;;
    all)
      boot_snapshot
      toggle_runtime_test
      summary
      ;;
    *)
      echo "Usage: $0 [boot|runtime|all]"
      echo ""
      echo "Examples:"
      echo "  $0"
      echo "  $0 boot"
      echo "  AIOV2_CTL_CMD=./aiov2_ctl.py $0 runtime"
      exit 2
      ;;
  esac
}

main "$@"
```
