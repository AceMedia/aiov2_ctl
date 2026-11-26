import atexit
import collections
import ctypes
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import weakref

from . import power
from .bootstrap import Text, np
from .power import debug, log
from .preferences import PREFERENCES_DIR

# The only hardware this module drives is an RTL-SDR, which is receive only.
# Nothing here ever transmits.

DVB_MODULE_NAME = "dvb_usb_rtl28xxu"
SDR_MEMORY_PATH = os.path.join(PREFERENCES_DIR, "sdr_memory.json")

# R820T class tuners cover roughly this range without direct sampling.
MIN_TUNE_MHZ = 24.0
MAX_TUNE_MHZ = 1766.0

# An upconverter (such as the Nooelec Ham It Up) sits between the antenna and the dongle and shifts the
# input up by a fixed amount, so HF can be heard on a dongle that stops at 24 MHz. Everything the user
# sees and types is the real frequency; only the call to rtl_sdr adds the offset. The Ham It Up is a
# plain sum mixer, so the spectrum is not flipped.
HAM_IT_UP_MHZ = 125.0
UPCONVERTER_PRESETS = (("None", 0.0), ("Ham It Up (+125 MHz)", HAM_IT_UP_MHZ))
MAX_UPCONVERTER_MHZ = 1000.0
HF_START_MHZ = 7.1


def load_upconverter_mhz():
    """The saved upconverter offset in MHz, 0 when there is none or the file is odd."""
    from .preferences import load_preferences
    try:
        value = float(load_preferences().get("sdr_upconverter_mhz", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return value if 0.0 <= value <= MAX_UPCONVERTER_MHZ else 0.0


def save_upconverter_mhz(mhz):
    from .preferences import load_preferences, save_preferences
    data = load_preferences()
    data["sdr_upconverter_mhz"] = float(mhz)
    return save_preferences(data)


def parse_upconverter_arg(text):
    """MHz from 'off', 'ham-it-up' or a number such as '125', else None."""
    word = (text or "").strip().lower().replace(" ", "").replace("_", "-")
    if word in ("off", "none", "no", "0"):
        return 0.0
    if word in ("ham-it-up", "hamitup", "ham"):
        return HAM_IT_UP_MHZ
    try:
        value = float(word.rstrip("mhz"))
    except ValueError:
        return None
    return value if 0.0 < value <= MAX_UPCONVERTER_MHZ else None


def tune_limits(offset_mhz=0.0):
    """(lowest, highest) frequency you can ask for, in the real frequencies the user sees."""
    return max(0.01, MIN_TUNE_MHZ - offset_mhz), MAX_TUNE_MHZ - offset_mhz


_FREQ_RE = re.compile(r"^([0-9]*\.?[0-9]+)(ghz|mhz|khz|hz|g|m|k)?$")


def parse_frequency_mhz(text, offset_mhz=0.0):
    """Parse typed frequency entry into MHz, or None if it is not usable.

    "98.5", "98.5m", "145.5 MHz", "145500k", "145500000hz" and "1.09g" all work.
    With no unit, values up to 2000 are MHz, up to 2,000,000 kHz, else Hz.
    Anything outside what an RTL-SDR can tune (24 to 1766 MHz, less any upconverter offset) is rejected.
    """
    match = _FREQ_RE.match((text or "").strip().lower().replace(" ", "").replace(",", ""))
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit in ("ghz", "g"):
        mhz = value * 1000.0
    elif unit in ("mhz", "m"):
        mhz = value
    elif unit in ("khz", "k"):
        mhz = value / 1000.0
    elif unit == "hz":
        mhz = value / 1e6
    elif value <= 2000:
        mhz = value
    elif value <= 2_000_000:
        mhz = value / 1000.0
    else:
        mhz = value / 1e6
    low, high = tune_limits(offset_mhz)
    if not (low <= mhz <= high):
        return None
    return round(mhz, 6)


_CB_RE = re.compile(r"^(?:cb|ch|c)\s*([0-9]{1,2})$")


def parse_cb_channel(text):
    """'ch19', 'cb 9' or 'c40' to a CB channel number from 1 to 40, else None."""
    match = _CB_RE.match((text or "").strip().lower())
    if not match:
        return None
    number = int(match.group(1))
    return number if 1 <= number <= 40 else None


def level_bar(snr_db, width=16, full_db=40.0):
    """Small text bar for signal above the noise floor."""
    if snr_db is None:
        return "\u2591" * width
    filled = int(round(max(0.0, min(1.0, snr_db / full_db)) * width))
    return "\u2588" * filled + "\u2591" * (width - filled)

try:
    _PRCTL = ctypes.CDLL(None, use_errno=True).prctl
except (OSError, AttributeError):
    _PRCTL = None


def _die_with_parent():
    """preexec_fn: have the kernel SIGTERM this child if ops dies without cleaning up.

    Covers SIGKILL and a dropped SSH session, which skip atexit. Linux only;
    a no-op elsewhere. Kept to a single C call so it is safe after fork.
    """
    if _PRCTL is not None:
        _PRCTL(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


def dvb_driver_loaded(modules_path="/proc/modules"):
    """True if the kernel DVB driver that grabs RTL dongles is loaded."""
    try:
        with open(modules_path, "r", encoding="utf-8") as handle:
            return any(line.split(" ", 1)[0] == DVB_MODULE_NAME for line in handle)
    except OSError:
        return False


def classify_rtl_error(text, dvb_loaded=False):
    """Turn rtl-sdr tool stderr into (kind, user message), or (None, None) if clean.

    kinds: missing, dvb, busy, permission, dropout, open_failed
    """
    text = text or ""
    low = text.lower()
    if "no supported devices found" in low or "no matching devices" in low:
        return "missing", "No RTL-SDR found on USB. Check the dongle and the SDR power rail"
    if "kernel driver is active" in low or (dvb_loaded and "usb_claim_interface" in low):
        return "dvb", f"Kernel DVB driver has the dongle. Blacklist {DVB_MODULE_NAME} and replug"
    if "usb_claim_interface" in low or "device or resource busy" in low:
        return "busy", "Dongle is busy. Another program (readsb, rtl_fm, gqrx) has it open"
    if "usb_open error -3" in low or "permission denied" in low:
        return "permission", "No permission to open the dongle. Check the rtl-sdr udev rules and plugdev group"
    if (
        "synchronous read failed" in low
        or "async read stalled" in low
        or "no such device" in low
        or "libusb_error_no_device" in low
        or "usb_bulk" in low
    ):
        return "dropout", "Dongle dropped out (USB read failed). Replug or check the SDR rail"
    if "failed to open rtlsdr device" in low:
        return "open_failed", "Could not open the RTL-SDR"
    return None, None


def terminate_process(proc, timeout=0.5):
    """Stop a child, always reap it and close its pipes. Safe to call twice."""
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
        else:
            proc.wait(timeout=0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        debug(f"SDR: could not reap pid {getattr(proc, 'pid', '?')}: {exc}")
    finally:
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except (OSError, ValueError):
                pass


def _shutdown_ref(ref):
    sdr = ref()
    if sdr is not None:
        sdr.shutdown()

class SDR:
    FM_PRESETS = [
        ("Radio 2", 88.1),
        ("Classic", 100.6),
        ("Radio 1", 98.8),
        ("Heart", 106.2),
        ("Smooth", 102.2),
        ("Talk", 105.3),
    ]
    UK_CB_CHANNELS = [
        {"channel": i + 1, "freq_mhz": 27.60125 + (i * 0.01)}
        for i in range(40)
    ]
    CEPT_CB_CHANNELS = [
        {"channel": 1, "freq_mhz": 26.965},
        {"channel": 2, "freq_mhz": 26.975},
        {"channel": 3, "freq_mhz": 26.985},
        {"channel": 4, "freq_mhz": 27.005},
        {"channel": 5, "freq_mhz": 27.015},
        {"channel": 6, "freq_mhz": 27.025},
        {"channel": 7, "freq_mhz": 27.035},
        {"channel": 8, "freq_mhz": 27.055},
        {"channel": 9, "freq_mhz": 27.065},
        {"channel": 10, "freq_mhz": 27.075},
        {"channel": 11, "freq_mhz": 27.085},
        {"channel": 12, "freq_mhz": 27.105},
        {"channel": 13, "freq_mhz": 27.115},
        {"channel": 14, "freq_mhz": 27.125},
        {"channel": 15, "freq_mhz": 27.135},
        {"channel": 16, "freq_mhz": 27.155},
        {"channel": 17, "freq_mhz": 27.165},
        {"channel": 18, "freq_mhz": 27.175},
        {"channel": 19, "freq_mhz": 27.185},
        {"channel": 20, "freq_mhz": 27.205},
        {"channel": 21, "freq_mhz": 27.215},
        {"channel": 22, "freq_mhz": 27.225},
        {"channel": 23, "freq_mhz": 27.255},
        {"channel": 24, "freq_mhz": 27.235},
        {"channel": 25, "freq_mhz": 27.245},
        {"channel": 26, "freq_mhz": 27.265},
        {"channel": 27, "freq_mhz": 27.275},
        {"channel": 28, "freq_mhz": 27.285},
        {"channel": 29, "freq_mhz": 27.295},
        {"channel": 30, "freq_mhz": 27.305},
        {"channel": 31, "freq_mhz": 27.315},
        {"channel": 32, "freq_mhz": 27.325},
        {"channel": 33, "freq_mhz": 27.335},
        {"channel": 34, "freq_mhz": 27.345},
        {"channel": 35, "freq_mhz": 27.355},
        {"channel": 36, "freq_mhz": 27.365},
        {"channel": 37, "freq_mhz": 27.375},
        {"channel": 38, "freq_mhz": 27.385},
        {"channel": 39, "freq_mhz": 27.395},
        {"channel": 40, "freq_mhz": 27.405},
    ]
    BAND_PROFILES = [
        {"name": "FM BC", "freq_mhz": 98.0, "modulation": "WFM", "if_bw_khz": 200, "range_low_mhz": 87.5, "range_high_mhz": 108.0, "candidate_spacing_mhz": 0.15, "freq_digits": 2},
        {"name": "Airband", "freq_mhz": 121.5, "modulation": "AM", "if_bw_khz": 8, "range_low_mhz": 118.0, "range_high_mhz": 137.0, "candidate_spacing_mhz": 0.025, "freq_digits": 3},
        {"name": "NOAA WX", "freq_mhz": 162.55, "modulation": "NFM", "if_bw_khz": 12, "range_low_mhz": 162.4, "range_high_mhz": 162.6, "candidate_spacing_mhz": 0.01, "freq_digits": 3},
        {"name": "2m Ham", "freq_mhz": 145.5, "modulation": "NFM", "if_bw_khz": 12, "range_low_mhz": 144.0, "range_high_mhz": 148.0, "candidate_spacing_mhz": 0.025, "freq_digits": 3},
        {"name": "70cm Ham", "freq_mhz": 433.5, "modulation": "NFM", "if_bw_khz": 12, "range_low_mhz": 430.0, "range_high_mhz": 440.0, "candidate_spacing_mhz": 0.025, "freq_digits": 3},
        {"name": "UK CB FM", "freq_mhz": 27.78125, "modulation": "NFM", "if_bw_khz": 10, "range_low_mhz": 27.60125, "range_high_mhz": 27.99125, "candidate_spacing_mhz": 0.01, "freq_digits": 5, "channel_plan": "UK"},
        {"name": "CEPT CB", "freq_mhz": 27.185, "modulation": "NFM", "if_bw_khz": 10, "range_low_mhz": 26.965, "range_high_mhz": 27.405, "candidate_spacing_mhz": 0.01, "freq_digits": 3, "channel_plan": "CEPT"},
        {"name": "CB 11m", "freq_mhz": 27.185, "modulation": "AM", "if_bw_khz": 10, "range_low_mhz": 26.9, "range_high_mhz": 27.5, "candidate_spacing_mhz": 0.01, "freq_digits": 3},
        {"name": "10m USB", "freq_mhz": 28.4, "modulation": "USB", "if_bw_khz": 3, "range_low_mhz": 28.0, "range_high_mhz": 29.7, "candidate_spacing_mhz": 0.01, "freq_digits": 3},
        {"name": "40m LSB", "freq_mhz": 7.2, "modulation": "LSB", "if_bw_khz": 3, "range_low_mhz": 7.0, "range_high_mhz": 7.3, "candidate_spacing_mhz": 0.005, "freq_digits": 3},
        # UK and EU additions. All receive only, public broadcast or licence free use.
        {"name": "Marine VHF", "freq_mhz": 156.8, "modulation": "NFM", "if_bw_khz": 16, "range_low_mhz": 156.0, "range_high_mhz": 162.025, "candidate_spacing_mhz": 0.025, "freq_digits": 3},
        {"name": "PMR446", "freq_mhz": 446.00625, "modulation": "NFM", "if_bw_khz": 10, "range_low_mhz": 446.0, "range_high_mhz": 446.2, "candidate_spacing_mhz": 0.0125, "freq_digits": 5, "channel_plan": "PMR446"},
        {"name": "Mil Air UHF", "freq_mhz": 243.0, "modulation": "AM", "if_bw_khz": 8, "range_low_mhz": 225.0, "range_high_mhz": 400.0, "candidate_spacing_mhz": 0.025, "freq_digits": 3},
        {"name": "WX Sat 137", "freq_mhz": 137.62, "modulation": "NFM", "if_bw_khz": 40, "range_low_mhz": 137.0, "range_high_mhz": 138.0, "candidate_spacing_mhz": 0.02, "freq_digits": 3},
        {"name": "6m Ham", "freq_mhz": 51.5, "modulation": "NFM", "if_bw_khz": 12, "range_low_mhz": 50.0, "range_high_mhz": 52.0, "candidate_spacing_mhz": 0.025, "freq_digits": 3},
        {"name": "4m Ham", "freq_mhz": 70.45, "modulation": "NFM", "if_bw_khz": 12, "range_low_mhz": 70.0, "range_high_mhz": 70.5, "candidate_spacing_mhz": 0.025, "freq_digits": 3},
        # Short wave and amateur bands below 24 MHz. They only appear when an upconverter makes them tunable.
        {"name": "MW AM", "freq_mhz": 0.693, "modulation": "AM", "if_bw_khz": 9, "range_low_mhz": 0.531, "range_high_mhz": 1.602, "candidate_spacing_mhz": 0.009, "freq_digits": 3},
        {"name": "160m", "freq_mhz": 1.85, "modulation": "LSB", "if_bw_khz": 3, "range_low_mhz": 1.81, "range_high_mhz": 2.0, "candidate_spacing_mhz": 0.005, "freq_digits": 3},
        {"name": "80m LSB", "freq_mhz": 3.65, "modulation": "LSB", "if_bw_khz": 3, "range_low_mhz": 3.5, "range_high_mhz": 3.8, "candidate_spacing_mhz": 0.005, "freq_digits": 3},
        {"name": "SW 49m", "freq_mhz": 6.05, "modulation": "AM", "if_bw_khz": 8, "range_low_mhz": 5.9, "range_high_mhz": 6.2, "candidate_spacing_mhz": 0.005, "freq_digits": 3},
        {"name": "SW 31m", "freq_mhz": 9.6, "modulation": "AM", "if_bw_khz": 8, "range_low_mhz": 9.4, "range_high_mhz": 9.9, "candidate_spacing_mhz": 0.005, "freq_digits": 3},
        {"name": "20m USB", "freq_mhz": 14.2, "modulation": "USB", "if_bw_khz": 3, "range_low_mhz": 14.0, "range_high_mhz": 14.35, "candidate_spacing_mhz": 0.005, "freq_digits": 3},
        {"name": "SW 19m", "freq_mhz": 15.4, "modulation": "AM", "if_bw_khz": 8, "range_low_mhz": 15.1, "range_high_mhz": 15.8, "candidate_spacing_mhz": 0.005, "freq_digits": 3},
        {"name": "15m USB", "freq_mhz": 21.2, "modulation": "USB", "if_bw_khz": 3, "range_low_mhz": 21.0, "range_high_mhz": 21.45, "candidate_spacing_mhz": 0.005, "freq_digits": 3},
    ]
    PMR446_CHANNELS = [
        {"channel": i + 1, "freq_mhz": round(446.00625 + (i * 0.0125), 5)}
        for i in range(16)
    ]

    def __init__(self):
        self.upconverter_mhz = load_upconverter_mhz()
        self.active = False
        self.status_reason = "No RTL-SDR device or tools"
        self.conflict_processes = []
        self.proc = None
        self.freq_mhz = 98.0  # Start at FM radio band
        self.center_mhz = self.freq_mhz  # hardware tuned centre
        self.tune_target_mhz = self.freq_mhz  # UI requested target
        self.audio_target_mhz = self.freq_mhz  # audio target within span
        self.signal_db = None
        self.scan_enabled = False
        self.spectrum = []  # Store recent spectrum data
        self.spectrum_history = []  # For waterfall
        self.max_history = 10
        self.audio_proc = None
        self.audio_enabled = False
        self.paplay_proc = None
        self.audio_thread = None
        self.audio_stop = threading.Event()
        self.demod_running = False
        self.audio_offset_hz = 0.0
        self.audio_phase = 0.0
        self.audio_noise_est = None
        self._audio_last_sample = None
        self._audio_fir_state = None
        self._audio_taps = None
        self._audio_taps_cutoff = None
        self._audio_deemph_state = 0.0
        self.squelch_level = 0  # 0 = off, higher = more aggressive
        self.overview_spectrum = []  # Wider view for navigation
        self.follow_tune = True  # keep tuned freq centred in the overview window
        self.overview_range = 5.0  # MHz window size (what you want visible)
        self.overview_center = 98.0  # will get overwritten on start/tune if follow_tune=True
        self.changing_freq = False  # Flag to pause spectrum during freq changes
        self.pending_freq = None
        self.last_tune_request = 0
        self.tune_delay = 0.25  # seconds
        self.last_spectrum_update = 0
        self.spectrum_interval = 1.0  # seconds
        self.last_line_time = 0
        self.last_scan_start_time = 0
        self.last_scan_restart = 0
        self.min_restart_gap = 0.5
        self.dirty = False
        self.bias_t_enabled = False
        self.rtlsdr_device = os.environ.get("RTLSDR_DEVICE")
        self.scan_low_hz = None
        self.scan_high_hz = None
        self.scan_seek_dir = 0
        self.scan_seek_threshold = -30
        self.audio_gain = 20  # dB
        self.cursor_idx = None
        self.bandwidth_mhz = 1.0
        self.seek_active = False
        self.seek_dir = 0
        self.seek_threshold = -30
        self.modulations = ["WFM", "FM", "NFM", "AM", "DSB", "USB", "LSB", "CW"]
        self.mod_index = 0
        self.modulation = self.modulations[0]
        self.if_bandwidths = {
            "WFM": 200,
            "FM": 150,
            "NFM": 12,
            "AM": 10,
            "DSB": 6,
            "USB": 3,
            "LSB": 3,
            "CW": 1,
        }
        self.if_bw_khz = self.if_bandwidths[self.modulation]
        self.panadapter_proc = None
        self.panadapter_fd = None
        self.panadapter_start_time = 0.0
        self.retune_start_time = 0.0
        self.retune_watchdog_fired = False
        self.fft_size = 2048
        self.sample_rate = 2400000
        self.audio_rate = 48000
        self.iq_chunk_samples = 24000
        self.audio_decim = max(1, int(self.sample_rate / self.audio_rate))
        self.audio_chunk_bytes = self.iq_chunk_samples * 2
        self.max_iq_buffer_seconds = 0.7
        self.iq_ring = collections.deque(
            maxlen=max(1, int((self.sample_rate * self.max_iq_buffer_seconds) / self.iq_chunk_samples))
        )
        self.iq_lock = threading.Lock()
        self.iq_cond = threading.Condition(self.iq_lock)
        self.last_fft_time = 0
        self.panadapter_interval = 0.2
        self.initial_fft_done = False
        self.last_fft_success_time = 0
        self._iq_buf = bytearray()
        self._audio_buf = bytearray()
        self.spectrum_center_mhz = None  # centre freq that self.spectrum was captured at
        self.capture_floor_db = None
        self.capture_ceil_db = None
        self.freeze_scale = False
        self.scale_floor_db = None
        self.scale_ceil_db = None
        self.peaks = []
        self.peak_threshold_db = 6.0
        self.peak_prominence_db = 4.0
        self.s_meter_db = None
        self.hold_max = []
        self.hold_decay = 0.995
        self.pending_recenter = True
        self.preset_name = None
        self.band_profile_name = None
        self.station_candidates = []
        self.last_bias_t_error = None
        self.last_error = None  # latest user-facing device or audio problem
        self.link_state = "ok"  # ok | retrying | lost
        self.device_failures = 0
        self.max_device_failures = 6
        self.next_restart_at = 0.0
        self.audio_error = None
        self.squelch_open = True
        self._pan_stderr = None
        self._shut_down = False
        self.band_memory = {}  # band name -> last tuned MHz
        self.last_freq_mhz = None  # where to start next time
        self.last_cb_profile = "UK CB FM"
        self.seek_windows_left = 0
        self._memory_dirty = False
        self._squelch_hang_until = 0.0
        self.squelch_hang_s = 0.25
        self.squelch_hysteresis_db = 1.5

        self._update_audio_offset()
        self._update_audio_filter()
        self._load_band_memory()

        self.conflict_processes = self._detect_conflicts()
        if self.conflict_processes:
            labels = ", ".join(self.conflict_processes[:3])
            self.status_reason = f"Busy: {labels}"
            debug(f"SDR: conflict detected ({labels})")
            log(f"SDR: busy - {labels}")
            return

        atexit.register(_shutdown_ref, weakref.ref(self))

        try:
            result = subprocess.run(
                ["rtl_test", "-t"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=1
            )
        except FileNotFoundError:
            self.status_reason = "rtl-sdr tools not installed (apt install rtl-sdr)"
            debug("SDR: rtl-sdr tools not found")
            log(f"SDR: {self.status_reason}")
        except subprocess.TimeoutExpired:
            self.status_reason = "RTL-SDR did not respond"
            debug("SDR: rtl_test timed out")
            log("SDR: No device or rtl-sdr tools not installed")
        else:
            kind, message = classify_rtl_error(result.stderr, dvb_driver_loaded())
            if kind in ("missing", "dvb", "busy", "permission", "open_failed"):
                self.status_reason = message
                debug(f"SDR: rtl_test reported {kind}")
                log(f"SDR: {message}")
            else:
                self.active = True
                self.status_reason = "RTL-SDR ready"
                debug("SDR: rtl-sdr detected")
                log("SDR: RTL-SDR device detected")

    def _detect_conflicts(self):
        known_names = {
            "readsb",
            "dump1090-fa",
            "dump1090",
            "rtl_tcp",
            "rtl_fm",
            "rtl_433",
            "sdrpp",
            "cubic_sdr",
            "gqrx",
        }
        conflicts = []
        try:
            result = subprocess.run(
                ["ps", "-eo", "comm="],
                capture_output=True,
                text=True,
                timeout=1,
            )
        except Exception:
            return conflicts

        for line in result.stdout.splitlines():
            name = line.strip()
            if name in known_names and name not in conflicts:
                conflicts.append(name)
        return conflicts
    
    def update_overview_window(self, target_freq):
        """Keep tuned frequency centred in the visible spectrum window."""
        if self.follow_tune:
            self.overview_center = float(target_freq)
            return

        window_half = self.overview_range / 2
        window_start = self.overview_center - window_half
        window_end = self.overview_center + window_half

        if target_freq < window_start:
            self.overview_center -= window_half
        elif target_freq > window_end:
            self.overview_center += window_half

    def current_preset(self):
        if not self.FM_PRESETS:
            return None
        return min(self.FM_PRESETS, key=lambda item: abs(item[1] - self.freq_mhz))

    def channel_plan(self, profile=None):
        profile = profile or self.active_band_profile()
        if not profile:
            return []
        plan_name = profile.get("channel_plan")
        if plan_name == "UK":
            return self.UK_CB_CHANNELS
        if plan_name == "CEPT":
            return self.CEPT_CB_CHANNELS
        if plan_name == "PMR446":
            return self.PMR446_CHANNELS
        return []

    def current_channel(self, profile=None):
        channels = self.channel_plan(profile)
        if not channels:
            return None
        nearest = min(channels, key=lambda item: abs(item["freq_mhz"] - self.freq_mhz))
        tolerance = 0.006 if profile and profile.get("channel_plan") == "UK" else 0.01
        if abs(nearest["freq_mhz"] - self.freq_mhz) <= tolerance:
            return nearest
        return nearest

    def current_band_profile(self):
        if not self.BAND_PROFILES:
            return None
        matches = [
            profile for profile in self.BAND_PROFILES
            if profile["modulation"] == self.modulation
        ]
        if not matches:
            matches = self.BAND_PROFILES
        return min(matches, key=lambda item: abs(item["freq_mhz"] - self.freq_mhz))

    def active_band_profile(self):
        if self.band_profile_name:
            for profile in self.BAND_PROFILES:
                if profile["name"] == self.band_profile_name:
                    return profile
        return self.current_band_profile()

    def set_modulation(self, modulation, if_bw_khz=None):
        if modulation not in self.modulations:
            return False
        self.mod_index = self.modulations.index(modulation)
        self.modulation = self.modulations[self.mod_index]
        self.if_bw_khz = int(if_bw_khz if if_bw_khz is not None else self.if_bandwidths[self.modulation])
        self.bandwidth_mhz = self.if_bw_khz / 1000.0
        self._update_audio_filter()
        self._audio_last_sample = None
        self._audio_fir_state = None
        self._audio_deemph_state = 0.0
        self.dirty = True
        return True

    def _load_band_memory(self):
        try:
            with open(SDR_MEMORY_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            debug(f"SDR: could not read band memory: {exc}")
            return
        last = data.get("last") if isinstance(data, dict) else None
        if isinstance(last, (int, float)) and tune_limits(self.upconverter_mhz)[0] <= last <= tune_limits(self.upconverter_mhz)[1]:
            self.last_freq_mhz = float(last)
        saved = data.get("bands") if isinstance(data, dict) else None
        if not isinstance(saved, dict):
            return
        for profile in self.BAND_PROFILES:
            freq = saved.get(profile["name"])
            if isinstance(freq, (int, float)) and self._in_profile_range(profile, freq):
                self.band_memory[profile["name"]] = float(freq)

    def save_band_memory(self):
        if not self._memory_dirty:
            return
        tmp = SDR_MEMORY_PATH + ".tmp"
        try:
            os.makedirs(os.path.dirname(SDR_MEMORY_PATH), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"bands": self.band_memory, "last": self.last_freq_mhz}, handle, indent=2)
            os.replace(tmp, SDR_MEMORY_PATH)
            self._memory_dirty = False
        except OSError as exc:
            debug(f"SDR: could not save band memory: {exc}")

    @staticmethod
    def _in_profile_range(profile, freq_mhz):
        return profile["range_low_mhz"] - 1e-9 <= freq_mhz <= profile["range_high_mhz"] + 1e-9

    def _remember_frequency(self, freq_mhz):
        if self.last_freq_mhz is None or abs(self.last_freq_mhz - freq_mhz) > 1e-9:
            self.last_freq_mhz = round(float(freq_mhz), 6)
            self._memory_dirty = True
        profile = self.active_band_profile()
        if not profile or not self._in_profile_range(profile, freq_mhz):
            return
        if abs(self.band_memory.get(profile["name"], -1.0) - freq_mhz) > 1e-9:
            self.band_memory[profile["name"]] = round(float(freq_mhz), 6)
            self._memory_dirty = True

    def tune_limits(self):
        return tune_limits(self.upconverter_mhz)

    def hw_mhz(self, shown_mhz):
        """What the dongle is told to tune to: the real frequency plus any upconverter offset."""
        return shown_mhz + self.upconverter_mhz

    def band_profiles(self):
        """The bands you can actually tune right now, so short wave only shows with an upconverter."""
        low, high = self.tune_limits()
        return [p for p in self.BAND_PROFILES if low <= p["freq_mhz"] <= high]

    def set_upconverter(self, mhz):
        """Set the upconverter offset (0 for none), remember it, and retune so it takes effect."""
        try:
            mhz = round(float(mhz), 6)
        except (TypeError, ValueError):
            return False
        if not (0.0 <= mhz <= MAX_UPCONVERTER_MHZ):
            return False
        self.upconverter_mhz = mhz
        save_upconverter_mhz(mhz)
        low, high = self.tune_limits()
        target = self.pending_freq if self.pending_freq is not None else self.freq_mhz
        if not (low <= target <= high):
            target = HF_START_MHZ if mhz > 0 else 98.0
            self.apply_startup_profile(target)
        self.request_tune(target, recenter=True)
        return True

    def profile_for_frequency(self, freq_mhz):
        """Narrowest band profile that contains the frequency, or None."""
        inside = [p for p in self.band_profiles() if self._in_profile_range(p, freq_mhz)]
        if not inside:
            return None
        return min(inside, key=lambda p: p["range_high_mhz"] - p["range_low_mhz"])

    def tune_to_frequency(self, text):
        """Tune to a typed frequency. Returns the MHz tuned, or None if unusable."""
        if not self.active:
            return None
        channel = parse_cb_channel(text)
        if channel is not None:
            picked = self.tune_to_cb_channel(channel)
            return picked["freq_mhz"] if picked else None
        freq = parse_frequency_mhz(text, self.upconverter_mhz)
        if freq is None:
            return None
        profile = self.profile_for_frequency(freq)
        current = self.active_band_profile()
        if profile and (current is None or profile["name"] != current["name"] or not self.band_profile_name):
            self.band_profile_name = profile["name"]
            self.set_modulation(profile["modulation"], profile.get("if_bw_khz"))
        elif not profile:
            self.band_profile_name = None
        self.preset_name = None
        self.request_tune(freq, recenter=True)
        return freq

    def apply_band_profile(self, profile):
        if not profile:
            return None
        self.save_band_memory()
        self.band_profile_name = profile["name"]
        self.preset_name = None
        self.set_modulation(profile["modulation"], profile.get("if_bw_khz"))
        remembered = self.band_memory.get(profile["name"])
        if remembered is not None and self._in_profile_range(profile, remembered):
            self.request_tune(remembered, recenter=True)
        else:
            self.request_tune(profile["freq_mhz"], recenter=True)
        return profile

    CB_PROFILE_NAMES = ("UK CB FM", "CEPT CB", "CB 11m")

    def startup_frequency(self):
        """Where to start: the last frequency used, else the FM broadcast band."""
        if self.last_freq_mhz is not None:
            return self.last_freq_mhz
        return HF_START_MHZ if self.upconverter_mhz > 0 else 98.0

    def apply_startup_profile(self, freq_mhz):
        """Match the modulation to the band the start frequency is in."""
        profile = self.profile_for_frequency(freq_mhz)
        if profile:
            self.band_profile_name = profile["name"]
            self.set_modulation(profile["modulation"], profile.get("if_bw_khz"))
        return profile

    def _cb_profile(self, name):
        for profile in self.BAND_PROFILES:
            if profile["name"] == name:
                return profile
        return None

    def cycle_cb(self, step=1):
        """One key to reach CB: first press goes to the last CB plan used, then it cycles
        UK 27/81 FM, CEPT FM and 11m AM."""
        if not self.active:
            return None
        current = self.active_band_profile()
        if current and current["name"] in self.CB_PROFILE_NAMES:
            index = self.CB_PROFILE_NAMES.index(current["name"])
            name = self.CB_PROFILE_NAMES[(index + step) % len(self.CB_PROFILE_NAMES)]
        else:
            name = self.last_cb_profile
        profile = self._cb_profile(name)
        if profile is None:
            return None
        self.last_cb_profile = name
        return self.apply_band_profile(profile)

    def tune_to_cb_channel(self, number):
        """Tune to CB channel 1 to 40 on the current CB plan (UK 27/81 unless CEPT is active)."""
        if not self.active:
            return None
        current = self.active_band_profile()
        name = current["name"] if current and current.get("channel_plan") in ("UK", "CEPT") else self.last_cb_profile
        profile = self._cb_profile(name)
        if profile is None or not profile.get("channel_plan"):
            profile = self._cb_profile("UK CB FM")
            name = "UK CB FM"
        for channel in self.channel_plan(profile):
            if channel["channel"] == number:
                self.last_cb_profile = name
                self.band_profile_name = name
                self.preset_name = None
                self.set_modulation(profile["modulation"], profile.get("if_bw_khz"))
                self.request_tune(channel["freq_mhz"], recenter=True)
                return dict(channel, plan=profile["channel_plan"], band=name)
        return None

    def seek_begin(self, direction):
        """Start a seek that sweeps window by window through the whole current band."""
        profile = self.active_band_profile()
        span = (profile["range_high_mhz"] - profile["range_low_mhz"]) if profile else 20.0
        step = max(0.05, 0.8 * self.sample_rate / 1e6)
        self.seek_dir = 1 if direction > 0 else -1
        self.seek_active = True
        self.seek_windows_left = int(span / step) + 2

    def seek_next_window(self):
        """Move one window along, wrapping at the band edge. None when the sweep is done."""
        if self.seek_windows_left <= 0:
            self.seek_active = False
            return None
        self.seek_windows_left -= 1
        profile = self.active_band_profile()
        limits = self.tune_limits()
        low = profile["range_low_mhz"] if profile else limits[0]
        high = profile["range_high_mhz"] if profile else limits[1]
        step = max(0.05, 0.8 * self.sample_rate / 1e6)
        new = self.freq_mhz + self.seek_dir * step
        if new > high:
            new = low
        elif new < low:
            new = high
        new = round(new, 5)
        self.request_tune(new, recenter=True)
        return new

    def seek_step(self):
        """One step of an active seek, shared by the console and the app.

        Waits for the radio to settle, tunes to the first strong signal past the centre, or
        slides along the band. Returns a message when something happened, else None.
        """
        if not self.spectrum:
            return None
        if self.changing_freq or time.time() - self.last_tune_request < 0.8:
            return None
        width = len(self.spectrum)
        noise = sorted(self.spectrum)[int(width * 0.2)]
        threshold = noise + 10
        center = width // 2
        step = 1 if self.seek_dir > 0 else -1
        i = center + step
        while 0 <= i < width:
            if self.spectrum[i] > threshold:
                offset_hz = ((i - center) / width) * self.sample_rate
                tuned = round(self.freq_mhz + offset_hz / 1e6, 3)
                self.request_tune(tuned, recenter=True)
                self.stop_seek()
                return f"Seek hit {tuned:.3f} MHz"
            i += step
        if self.seek_next_window() is None:
            return "Seek found nothing in this band"
        return None

    def stop_seek(self):
        self.seek_active = False
        self.seek_windows_left = 0

    def tune_to_band_profile(self, step=1):
        profiles = self.band_profiles()
        if not self.active or not profiles:
            return None
        current = self.active_band_profile()
        if current is None:
            return None
        current_index = profiles.index(current) if current in profiles else -step if step > 0 else 0
        next_index = (current_index + step) % len(profiles)
        return self.apply_band_profile(profiles[next_index])

    def tune_to_channel(self, step=1):
        if not self.active:
            return None
        profile = self.active_band_profile()
        channels = self.channel_plan(profile)
        if not channels:
            return None
        current = self.current_channel(profile)
        current_index = channels.index(current) if current in channels else 0
        next_index = (current_index + step) % len(channels)
        selected = dict(channels[next_index])
        selected["plan"] = profile.get("channel_plan")
        selected["band"] = profile["name"]
        self.band_profile_name = profile["name"]
        self.preset_name = None
        self.set_modulation(profile["modulation"], profile.get("if_bw_khz"))
        self.request_tune(selected["freq_mhz"], recenter=True)
        return selected

    def _estimate_signal_db(self, freq_mhz):
        if not self.spectrum:
            return None
        center_mhz = self.spectrum_center_mhz if self.spectrum_center_mhz is not None else self.freq_mhz
        offset_mhz = freq_mhz - center_mhz
        span_mhz = self.sample_rate / 1e6
        if abs(offset_mhz) > (span_mhz / 2):
            return None
        size = len(self.spectrum)
        idx = int(round(((offset_mhz / span_mhz) * size) + (size / 2)))
        idx = max(0, min(size - 1, idx))
        return self.spectrum[idx]

    def tune_to_preset(self, step=1):
        if not self.active or not self.FM_PRESETS:
            return None

        current = self.current_preset()
        if current is None:
            return None

        current_index = self.FM_PRESETS.index(current)
        next_index = (current_index + step) % len(self.FM_PRESETS)
        preset_name, freq_mhz = self.FM_PRESETS[next_index]
        self.preset_name = preset_name
        self.band_profile_name = "FM BC"
        self.set_modulation("WFM")
        self.request_tune(freq_mhz, recenter=True)
        return preset_name, freq_mhz

    def strongest_peaks(self, limit=3):
        if not self.spectrum or not self.peaks:
            return []

        size = len(self.spectrum)
        center = size // 2
        center_mhz = self.spectrum_center_mhz if self.spectrum_center_mhz is not None else self.freq_mhz
        results = []

        for idx in self.peaks:
            if not (0 <= idx < size):
                continue
            offset_hz = ((idx - center) / size) * self.sample_rate
            freq_mhz = center_mhz + (offset_hz / 1e6)
            nearest_preset = None
            if self.FM_PRESETS:
                nearest_preset = min(self.FM_PRESETS, key=lambda item: abs(item[1] - freq_mhz))
            results.append(
                {
                    "freq_mhz": freq_mhz,
                    "db": self.spectrum[idx],
                    "preset": nearest_preset[0] if nearest_preset and abs(nearest_preset[1] - freq_mhz) <= 0.25 else None,
                }
            )

        results.sort(key=lambda item: item["db"], reverse=True)
        return results[:limit]

    def station_scan_candidates(self, limit=5):
        profile = self.active_band_profile()
        if profile:
            low_mhz = profile.get("range_low_mhz", self.freq_mhz - (self.sample_rate / 2e6))
            high_mhz = profile.get("range_high_mhz", self.freq_mhz + (self.sample_rate / 2e6))
            dedupe_gap = profile.get("candidate_spacing_mhz", 0.05)
            digits = profile.get("freq_digits", 2)
            label = profile["name"]
        else:
            low_mhz = self.freq_mhz - (self.sample_rate / 2e6)
            high_mhz = self.freq_mhz + (self.sample_rate / 2e6)
            dedupe_gap = 0.05
            digits = 2
            label = "Current"

        channels = self.channel_plan(profile)
        if channels:
            candidates = []
            for channel in channels:
                freq = channel["freq_mhz"]
                if not (low_mhz <= freq <= high_mhz):
                    continue
                db = self._estimate_signal_db(freq)
                if db is None:
                    continue
                candidates.append(
                    {
                        "freq_mhz": freq,
                        "db": db,
                        "band": label,
                        "freq_digits": digits,
                        "channel": channel["channel"],
                        "plan": profile.get("channel_plan"),
                        "tune_tolerance_mhz": max(0.001, dedupe_gap / 2.0),
                    }
                )
            candidates.sort(key=lambda item: item["db"], reverse=True)
            self.station_candidates = candidates[:limit]
            return list(self.station_candidates)

        candidates = []
        for peak in self.strongest_peaks(limit=max(limit * 2, limit)):
            freq = peak["freq_mhz"]
            if not (low_mhz <= freq <= high_mhz):
                continue
            peak = dict(peak)
            peak["band"] = label
            peak["freq_digits"] = digits
            peak["tune_tolerance_mhz"] = max(0.002, dedupe_gap / 2.0)
            candidates.append(peak)

        deduped = []
        for candidate in candidates:
            if any(abs(candidate["freq_mhz"] - existing["freq_mhz"]) < dedupe_gap for existing in deduped):
                continue
            deduped.append(candidate)

        deduped.sort(key=lambda item: item["db"], reverse=True)
        self.station_candidates = deduped[:limit]
        return list(self.station_candidates)

    def tune_to_station_candidate(self, step=1):
        candidates = self.station_scan_candidates(limit=5)
        if not self.active or not candidates:
            return None

        current_index = min(range(len(candidates)), key=lambda i: abs(candidates[i]["freq_mhz"] - self.freq_mhz))
        next_index = (current_index + step) % len(candidates)
        selected = candidates[next_index]
        profile = self.active_band_profile()
        self.preset_name = selected.get("preset")
        self.band_profile_name = profile["name"] if profile else None
        if profile:
            self.set_modulation(profile["modulation"], profile.get("if_bw_khz"))
        self.request_tune(selected["freq_mhz"], recenter=True)
        return selected
    
    def start_scan(self, freq_mhz=100.0, bandwidth=2.0, recenter=False, force=False):
        """Start scanning at specified frequency with bandwidth in MHz"""
        if not self.active:
            return False

        if not force and time.time() - self.last_scan_restart < self.min_restart_gap:
            return False

        now = time.time()
        self.last_scan_restart = now
        self.last_scan_start_time = now
        self.last_line_time = 0
        self.scan_low_hz = None
        self.scan_high_hz = None
        
        # Set flag to pause spectrum updates
        self.changing_freq = True
        self.dirty = True
        
        # Always recentre if follow_tune is enabled, or if explicitly requested
        if self.follow_tune or recenter:
            self.update_overview_window(freq_mhz)
        
        self.freq_mhz = freq_mhz
        self.center_mhz = freq_mhz
        self.tune_target_mhz = freq_mhz
        self.audio_target_mhz = freq_mhz
        self._update_audio_offset()
        self.scan_enabled = True

        try:
            self._iq_buf.clear()
            self._audio_buf.clear()
            with self.iq_cond:
                self.iq_ring.clear()
            self.stop_panadapter()
            self.start_panadapter()
            self.changing_freq = False
            self.initial_fft_done = False
            self.last_fft_time = 0
            self.last_fft_success_time = 0
            self.dirty = True
            return True
        except Exception as e:
            debug(f"SDR: panadapter start failed: {e}")
            self.changing_freq = False
            return False

    def stop_panadapter(self):
        proc = self.panadapter_proc
        if not proc:
            return
        try:
            terminate_process(proc)
        finally:
            self.panadapter_proc = None
            self.panadapter_fd = None
            self._close_pan_stderr()
            self._iq_buf.clear()
            self._audio_buf.clear()
            with self.iq_cond:
                self.iq_ring.clear()

    def _close_pan_stderr(self):
        if self._pan_stderr is not None:
            try:
                self._pan_stderr.close()
            except OSError:
                pass
            self._pan_stderr = None

    def _read_pan_stderr(self):
        """Last part of rtl_sdr's stderr, for error classification."""
        handle = self._pan_stderr
        if handle is None:
            return ""
        try:
            handle.flush()
            handle.seek(0)
            return handle.read()[-2000:].decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    def start_panadapter(self):
        if self.panadapter_proc:
            if self.panadapter_proc.poll() is None:
                return
            self.panadapter_proc = None
            self.panadapter_fd = None

        center = self.center_mhz if self.center_mhz is not None else self.freq_mhz

        self._close_pan_stderr()
        self._pan_stderr = tempfile.TemporaryFile()
        try:
            self.panadapter_proc = subprocess.Popen(
                [
                    "rtl_sdr",
                    "-f", str(int(self.hw_mhz(center) * 1e6)),
                    "-s", str(self.sample_rate),
                    "-g", "auto",
                    "-"
                ],
                stdout=subprocess.PIPE,
                stderr=self._pan_stderr,
                preexec_fn=_die_with_parent,
            )
        except OSError as exc:
            self._close_pan_stderr()
            if isinstance(exc, FileNotFoundError):
                self.last_error = "rtl_sdr not found (apt install rtl-sdr)"
            else:
                self.last_error = f"Could not start rtl_sdr: {exc}"
            self.link_state = "lost"
            log(f"SDR: {self.last_error}")
            raise

        self.panadapter_fd = self.panadapter_proc.stdout
        if self.panadapter_fd:
            os.set_blocking(self.panadapter_fd.fileno(), False)

        self.panadapter_start_time = time.time()

    def stop_scan(self):
        if self.proc:
            try:
                terminate_process(self.proc)
            finally:
                self.proc = None
        self.stop_panadapter()

    def shutdown(self):
        """Stop every child process and thread. Idempotent, also runs at exit.

        Does not touch bias-t or any power rail; that stays with the caller.
        """
        if self._shut_down:
            return
        self._shut_down = True
        self.save_band_memory()
        try:
            self.stop_audio()
        finally:
            self.stop_scan()
            self.scan_enabled = False

    def _handle_panadapter_exit(self):
        """rtl_sdr died on its own: reap it, say why, decide whether to retry."""
        proc = self.panadapter_proc
        code = proc.poll() if proc else None
        tail = self._read_pan_stderr()
        lived = time.time() - self.panadapter_start_time if self.panadapter_start_time else 0.0
        self.stop_panadapter()
        self.initial_fft_done = False
        self.last_fft_time = 0
        self.spectrum = []
        self.peaks = []
        self.s_meter_db = None
        self.signal_db = None

        kind, message = classify_rtl_error(tail, dvb_driver_loaded())
        if message is None:
            message = f"rtl_sdr exited (code {code}). Dongle may have dropped out"
        self.last_error = message
        self.device_failures = self.device_failures + 1 if lived < 8.0 else 1
        if self.device_failures >= self.max_device_failures or kind in ("missing", "dvb", "permission"):
            self.link_state = "lost"
            self.next_restart_at = 0.0
            log(f"SDR: {message}. Giving up, retune to retry")
        else:
            self.link_state = "retrying"
            self.next_restart_at = time.time() + min(10.0, 0.5 * (2 ** (self.device_failures - 1)))
            log(f"SDR: {message}. Retrying")
        self.dirty = True

    def _restart_allowed(self):
        if self.link_state == "lost":
            return False
        return time.time() >= self.next_restart_at

    def apply_pending_tune(self):
        if self.pending_freq is None:
            if self.changing_freq:
                self.changing_freq = False
                self.dirty = True
            return

        if time.time() - self.last_tune_request < self.tune_delay:
            return

        freq = self.pending_freq
        self.pending_freq = None
        recenter = self.pending_recenter
        span_hz = self.sample_rate / 2
        retune_hardware = (
            self.center_mhz is None
            or abs((freq - self.center_mhz) * 1e6) > (span_hz * 0.90)
        )

        if retune_hardware:
            self.on_retune_started()
            started = self.start_scan(freq, bandwidth=self.bandwidth_mhz, recenter=recenter, force=True)

            if not started:
                self.pending_freq = freq
                self.last_tune_request = time.time()
                return

            self.center_mhz = freq
            self.freq_mhz = freq
            self.tune_target_mhz = freq
            self.audio_target_mhz = freq
            self._update_audio_offset()
            self.on_retune_complete()
            self._remember_frequency(freq)
        else:
            if self.follow_tune or recenter:
                self.update_overview_window(freq)
            self.freq_mhz = freq
            self.tune_target_mhz = freq
            self.audio_target_mhz = freq
            self._update_audio_offset()
            self.changing_freq = False
            self.dirty = True
            self._remember_frequency(freq)
    
    def read(self):
        """Read current spectrum"""
        if not self.proc:
            return None
        
        # Skip reading while changing frequency to avoid crashes
        if self.changing_freq:
            return None
        
        try:
            import select

            now = time.time()
            if self.last_line_time == 0 and self.last_scan_start_time and (now - self.last_scan_start_time) > 4:
                debug("SDR: no spectrum output yet, restarting scan")
                self.start_scan(self.freq_mhz)
                return None
            if self.last_line_time and (now - self.last_line_time) > 5:
                debug("SDR: spectrum stalled, restarting scan")
                self.start_scan(self.freq_mhz)
                return None

            r, _, _ = select.select([self.proc.stdout], [], [], 0)
            if not r:
                return None

            line = self.proc.stdout.readline()
            if not line:
                return None
            
            # rtl_power output: date, time, Hz low, Hz high, Hz step, samples, dB values...
            self.last_line_time = time.time()
            parts = [part.strip() for part in line.strip().split(",")]
            now = time.time()
            if now - self.last_spectrum_update < self.spectrum_interval:
                return None

            if len(parts) > 6:
                try:
                    low_hz = float(parts[2])
                    high_hz = float(parts[3])
                    if high_hz > low_hz:
                        self.scan_low_hz = low_hz
                        self.scan_high_hz = high_hz
                except (ValueError, IndexError):
                    pass
                db_values = [float(x) for x in parts[6:]]
                self.spectrum = db_values
                self.signal_db = sum(db_values) / len(db_values)
                
                # Add to history for waterfall
                self.spectrum_history.append(db_values)
                if len(self.spectrum_history) > self.max_history:
                    self.spectrum_history.pop(0)
                
                # Update overview: store current spectrum in wider context
                # This is a simplified approach - just keep the current detailed scan as part of overview
                if len(self.overview_spectrum) == 0:
                    self.overview_spectrum = db_values[:]
                
                debug(f"SDR: {self.signal_db:.1f} dB avg, {len(db_values)} bins")
                self.last_spectrum_update = now
                self.dirty = True
                return self.signal_db
        except Exception as e:
            debug(f"SDR: read error: {e}")
        
        return None

    def read_panadapter(self):
        self._check_audio_health()
        if self.panadapter_proc and self.panadapter_proc.poll() is not None:
            self._handle_panadapter_exit()
            return

        if not self.panadapter_fd:
            if (
                self.link_state == "retrying"
                and not self.panadapter_proc
                and self._restart_allowed()
            ):
                self._iq_buf.clear()
                self.initial_fft_done = False
                self.last_fft_time = 0
                try:
                    self.start_panadapter()
                except OSError:
                    self.dirty = True
            return

        now = time.time()
        if (
            not self.initial_fft_done
            and self.panadapter_start_time
            and (now - self.panadapter_start_time) > 1.5
            and not self.retune_watchdog_fired
        ):
            self.retune_watchdog_fired = True
            debug("SDR: no FFT after panadapter start, restarting panadapter once")
            self._iq_buf.clear()
            self.stop_panadapter()
            self.start_panadapter()
            return
        if self.initial_fft_done:
            if now - self.last_fft_time < self.panadapter_interval:
                pass

        if self.panadapter_proc and self.panadapter_proc.poll() is None:
            if self.last_fft_success_time and (now - self.last_fft_success_time) > 2:
                self.stop_panadapter()
                self.start_panadapter()
                self.on_retune_started()
                self.on_retune_complete()
                return

        read_size = max(self.audio_chunk_bytes * 2, self.fft_size * 2 * 2)
        while True:
            try:
                raw = self.panadapter_fd.read(read_size)
            except BlockingIOError:
                break

            if not raw:
                break

            self._iq_buf.extend(raw)
            if self.audio_enabled or self.demod_running:
                self._audio_buf.extend(raw)

                if self.audio_chunk_bytes > 0:
                    while len(self._audio_buf) >= self.audio_chunk_bytes:
                        chunk = bytes(self._audio_buf[:self.audio_chunk_bytes])
                        del self._audio_buf[:self.audio_chunk_bytes]
                        with self.iq_cond:
                            self.iq_ring.append(chunk)
                            self.iq_cond.notify()

        if self.initial_fft_done and (now - self.last_fft_time) < self.panadapter_interval:
            return

        need = self.fft_size * 2 * 2
        if len(self._iq_buf) < need:
            return

        chunk = bytes(self._iq_buf[:need])
        del self._iq_buf[:need]

        iq = np.frombuffer(chunk, dtype=np.uint8).astype(np.float32)
        iq = iq - 127.5
        i = iq[0::2]
        q = iq[1::2]
        samples = i + 1j * q

        window = np.hanning(len(samples))
        fft = np.fft.fftshift(np.fft.fft(samples * window))
        power = 20 * np.log10(np.abs(fft) + 1e-6)

        self.spectrum = power.tolist()
        self.spectrum_center_mhz = self.center_mhz if self.center_mhz is not None else self.freq_mhz
        spec = self.spectrum
        self.capture_floor_db = min(spec) if spec else None
        self.capture_ceil_db = max(spec) if spec else None
        if self.capture_floor_db is not None and self.capture_ceil_db is not None:
            self.scale_floor_db = self.capture_floor_db
            self.scale_ceil_db = self.capture_ceil_db
            self.freeze_scale = False
        self.peaks = []

        if len(spec) >= 3:
            noise_floor = sorted(spec)[int(len(spec) * 0.2)]
            threshold = noise_floor + self.peak_threshold_db
            w = 8

            for i in range(1, len(spec) - 1):
                if spec[i] <= threshold:
                    continue
                if not (spec[i] > spec[i - 1] and spec[i] > spec[i + 1]):
                    continue

                left = spec[max(0, i - w):i]
                right = spec[i + 1:min(len(spec), i + w + 1)]
                if not left or not right:
                    continue
                local_base = max(max(left), max(right))
                if (spec[i] - local_base) < self.peak_prominence_db:
                    continue

                self.peaks.append(i)

        # --- cluster peaks ---
        clustered = []
        cluster_radius = 6  # bins

        for p in sorted(self.peaks):
            if not clustered or p - clustered[-1] > cluster_radius:
                clustered.append(p)
            else:
                if spec[p] > spec[clustered[-1]]:
                    clustered[-1] = p

        self.peaks = clustered

        center = len(spec) // 2
        if_bw_bins = int((self.if_bw_khz * 1000 / self.sample_rate) * len(spec))
        half = max(1, if_bw_bins // 2)
        lo = max(0, center - half)
        hi = min(len(spec), center + half)
        if hi > lo and not self.changing_freq:
            self.s_meter_db = sum(spec[lo:hi]) / (hi - lo)

        if not self.hold_max:
            self.hold_max = spec[:]
        elif len(self.hold_max) == len(spec):
            for i in range(len(spec)):
                self.hold_max[i] = max(
                    self.hold_max[i] * self.hold_decay,
                    spec[i]
                )
        else:
            self.hold_max = spec[:]
        self.initial_fft_done = True
        self.last_fft_time = now
        self.last_fft_success_time = now
        if self.link_state != "ok" or self.last_error:
            self.link_state = "ok"
            self.device_failures = 0
            self.last_error = None
        self.dirty = True

    def prime_spectrum(self):
        self.last_fft_time = 0
        self.read_panadapter()
        self.dirty = True

    def on_retune_started(self):
        self.changing_freq = True
        self.initial_fft_done = False
        self.last_fft_time = 0
        self.last_fft_success_time = 0
        self.retune_start_time = time.time()
        self.retune_watchdog_fired = False
        self.peaks = []
        self.s_meter_db = None
        self.hold_max = []
        self._iq_buf.clear()
        self.dirty = True

    def on_retune_complete(self):
        self.changing_freq = False
        self.initial_fft_done = False
        self.last_fft_time = 0
        self.last_fft_success_time = 0
        self.dirty = True

    def request_tune(self, new_freq_mhz, recenter=True):
        if self.link_state == "lost":
            # A tune is the user asking again, so allow a fresh attempt.
            self.link_state = "retrying"
            self.device_failures = 0
            self.next_restart_at = 0.0
        self.pending_freq = new_freq_mhz
        self.pending_recenter = recenter
        self.last_tune_request = time.time()
        self.changing_freq = True
        if self.center_mhz is None or abs((new_freq_mhz - self.center_mhz) * 1e6) <= (self.sample_rate / 2) * 0.90:
            self.audio_target_mhz = new_freq_mhz
            self._update_audio_offset()
        self.freeze_scale = True
        if self.capture_floor_db is not None and self.capture_ceil_db is not None:
            self.scale_floor_db = self.capture_floor_db
            self.scale_ceil_db = self.capture_ceil_db
        self.dirty = True

    def finish_startup_tune(self):
        """
        Ensure startup is not treated as an in-progress tune.
        Allows panadapter FFT to start immediately.
        """
        self.changing_freq = False
        self.pending_freq = None
    
    def get_spectrum_display(self, width=40, height=8, show_labels=True):
        """Generate ASCII spectrum display with optional frequency labels"""
        if not self.spectrum:
            return "Scanning..."

        # Normalize spectrum to current data range
        min_db = min(self.spectrum)
        max_db = max(self.spectrum)
        db_range = max_db - min_db if max_db > min_db else 1
        
        # Downsample spectrum to fit width
        bins_per_char = max(1, len(self.spectrum) // width)
        display_bins = []
        for i in range(0, len(self.spectrum), bins_per_char):
            chunk = self.spectrum[i:i+bins_per_char]
            avg = sum(chunk) / len(chunk)
            display_bins.append(avg)
        
        # Limit to width
        display_bins = display_bins[:width]
        
        # Build display
        lines = []
        for row in range(height):
            threshold = max_db - (row * db_range / height)
            line = ""
            for db in display_bins:
                if db >= threshold:
                    line += "█"
                elif db >= threshold - (db_range / height / 2):
                    line += "▄"
                else:
                    line += " "
            lines.append(line)
        
        # Add frequency labels and dB range if requested
        if show_labels and width >= 40:
            # Calculate frequency range
            bw_khz = self.scan_enabled if hasattr(self, 'scan_enabled') else 2000
            start_mhz = self.freq_mhz - 1.0  # Approximate based on 2 MHz default
            end_mhz = self.freq_mhz + 1.0
            
            # Build label line
            label = f"{start_mhz:.1f} MHz"
            center_label = f" {self.freq_mhz:.1f} "
            end_label = f"{end_mhz:.1f} MHz"
            
            # Center the center label
            left_padding = (width - len(center_label)) // 2
            right_padding = width - left_padding - len(center_label)
            
            # Build the label line with proper spacing
            if width >= 60:
                freq_line = label.ljust(left_padding) + center_label + end_label.rjust(right_padding)
            else:
                # Simplified for narrow displays
                freq_line = f"{self.freq_mhz:.1f} MHz".center(width)
            
            lines.append("─" * width)
            lines.append(freq_line[:width])
            
            # Add dB range info
            if width >= 50:
                db_info = f"Range: {min_db:.0f} to {max_db:.0f} dB"
                lines.append(db_info.center(width)[:width])
        
        return "\n".join(lines)

    def get_overview_bounds(self):
        if self.scan_low_hz is not None and self.scan_high_hz is not None:
            return self.scan_low_hz / 1e6, self.scan_high_hz / 1e6

        return (
            self.overview_center - (self.overview_range / 2),
            self.overview_center + (self.overview_range / 2),
        )
    
    def get_overview_display(self, width=40, height=8, bandwidth_mhz=1.0):
        """Generate overview spectrum showing current position within wider range"""
        span_mhz = self.sample_rate / 1e6
        display_freq = self.freq_mhz
        if self.changing_freq and self.pending_freq is not None:
            display_freq = self.pending_freq
        center_freq = self.spectrum_center_mhz if self.spectrum_center_mhz is not None else self.center_mhz
        if center_freq is None:
            center_freq = display_freq
        lock_spectrum = (
            not self.changing_freq
            and self.spectrum_center_mhz is not None
            and abs(display_freq - self.spectrum_center_mhz) * 1e6 <= (self.sample_rate / 2) * 0.90
        )
        overview_center = center_freq if lock_spectrum else display_freq
        overview_start = overview_center - (span_mhz / 2)
        overview_end = overview_center + (span_mhz / 2)
        overview_span = span_mhz

        CURSOR_COLOURS = {
            "WFM": "bright_blue",
            "FM": "bright_yellow",
            "NFM": "bright_yellow",
            "AM": "bright_magenta",
            "DSB": "bright_magenta",
            "USB": "bright_green",
            "LSB": "bright_green",
            "CW": "bright_red",
        }
        cursor_colour = CURSOR_COLOURS.get(self.modulation, "bright_white")
        cursor_marker = f"[{cursor_colour} bold]┃[/{cursor_colour} bold]"
        BW_BG = "on grey35"
        IF_BG = "on grey30"

        have_spectrum = bool(self.spectrum)
        
        # The spectrum data now spans the full overview range
        # Downsample to fit display width
        display_bins = []
        hold_bins = []
        peak_cols = set()
        shift_cols = 0
        if have_spectrum:
            n = len(self.spectrum)
            step = max(1, n // width)
            shift_bins = 0
            if n > 0 and self.spectrum_center_mhz is not None:
                delta_mhz = 0.0 if lock_spectrum else (display_freq - self.spectrum_center_mhz)
                shift_bins = int(round((delta_mhz / overview_span) * n))
            if step > 0:
                shift_cols = int(round(shift_bins / step))

            floor = (
                self.scale_floor_db
                if self.scale_floor_db is not None
                else (min(self.spectrum) if self.spectrum else -120.0)
            )

            for i in range(0, n, step):
                src_start = i + shift_bins
                src_end = src_start + step

                if src_end <= 0 or src_start >= n:
                    chunk = []
                    hm = []
                else:
                    a = max(0, src_start)
                    b = min(n, src_end)
                    chunk = self.spectrum[a:b]
                    hm = self.hold_max[a:b] if self.hold_max and b > a else []

                if chunk:
                    display_bins.append(sum(chunk) / len(chunk))
                    hold_bins.append(max(hm) if hm else None)
                else:
                    display_bins.append(floor)
                    hold_bins.append(None)
        else:
            display_bins = [0.0] * width
            hold_bins = [None] * width
        
        # Ensure we have exactly 'width' bins
        if len(display_bins) < width:
            # Pad with last value if needed
            while len(display_bins) < width:
                display_bins.append(display_bins[-1] if display_bins else -50)
                hold_bins.append(hold_bins[-1] if hold_bins else None)
        display_bins = display_bins[:width]
        hold_bins = hold_bins[:width]

        # ---- Peaks: map bins -> freq -> columns, include offscreen + labels ----
        n = len(self.spectrum)
        shift_bins = 0
        if n > 0 and self.spectrum_center_mhz is not None:
            delta_mhz = 0.0 if lock_spectrum else (display_freq - self.spectrum_center_mhz)
            shift_bins = int(round((delta_mhz / overview_span) * n))

        def bin_to_freq_mhz(bin_idx: int) -> float:
            # spectrum bins span [overview_start, overview_end]
            if n <= 1:
                return display_freq
            return overview_start + (bin_idx / (n - 1)) * overview_span

        def freq_to_col_clamped(freq_mhz: float) -> int:
            if overview_span <= 0:
                return 0
            col = int(round((freq_mhz - overview_start) / overview_span * (width - 1)))
            return max(0, min(width - 1, col))

        # Build list of peaks with frequencies and columns
        peak_points = []  # (freq_mhz, col, offscreen_dir)
        # offscreen_dir: -1 left, +1 right, 0 in-range
        if self.peaks and n > 1:
            for p in self.peaks:
                p_shifted = p - shift_bins
                f_mhz = bin_to_freq_mhz(p_shifted)
                off_dir = 0
                if f_mhz < overview_start:
                    off_dir = -1
                elif f_mhz > overview_end:
                    off_dir = 1
                col = freq_to_col_clamped(f_mhz)
                peak_points.append((f_mhz, col, off_dir))

        # Sort strictly by frequency so we can place labels in correct order
        peak_points.sort(key=lambda x: x[0])
        
        # Normalize to height
        if len(display_bins) > 0:
            if have_spectrum:
                if self.freeze_scale and self.scale_floor_db is not None and self.scale_ceil_db is not None:
                    min_db = self.scale_floor_db
                    max_db = self.scale_ceil_db
                elif self.changing_freq and self.capture_floor_db is not None and self.capture_ceil_db is not None:
                    min_db = self.capture_floor_db
                    max_db = self.capture_ceil_db
                else:
                    min_db = min(display_bins)
                    max_db = max(display_bins)
                db_range = max_db - min_db if max_db > min_db else 1
            else:
                min_db = 0.0
                max_db = 1.0
                db_range = 1.0
        else:
            min_db = 0.0
            max_db = 1.0
            db_range = 1.0
        
        cursor_pos = width // 2
        if lock_spectrum:
            offset_cols = int(round((display_freq - overview_center) / overview_span * (width - 1)))
            cursor_pos = max(0, min(width - 1, (width // 2) + offset_cols))
        mask_center = cursor_pos if not self.changing_freq else max(0, min(width - 1, cursor_pos - shift_cols))

        disp_bins_per_mhz = width / overview_span if overview_span else 0
        bw_cols = int(bandwidth_mhz * disp_bins_per_mhz) if disp_bins_per_mhz > 0 else 0
        half_bw = max(0, bw_cols // 2)
        if_bw_mhz = self.if_bw_khz / 1000.0
        if_bw_cols = int(if_bw_mhz * disp_bins_per_mhz) if disp_bins_per_mhz > 0 else 0
        half_if_bw = max(1, if_bw_cols // 2)

        # Build display
        lines = []

        # Marker line (directional markers)
        peak_line = [" "] * width
        for f_mhz, col, off_dir in peak_points:
            if off_dir == 0:
                peak_line[col] = "▲"
            elif off_dir < 0:
                peak_line[col] = "◀"
            else:
                peak_line[col] = "▶"
        lines.append("".join(peak_line))

        # Label line (frequencies next to the triangles), non-overlapping
        label_line = [" "] * width
        occupied = [False] * width

        def can_place(start: int, text: str) -> bool:
            end = start + len(text)
            if start < 0 or end > width:
                return False
            return all(not occupied[i] for i in range(start, end))

        def place_text(start: int, text: str):
            for i, ch in enumerate(text):
                idx = start + i
                label_line[idx] = ch
                occupied[idx] = True

        for f_mhz, col, off_dir in peak_points:
            # Frequency label (1dp is usually enough; bump to 2dp if you prefer)
            txt = f"{f_mhz:.1f}"

            # Prefer placing centred on the marker, but fall back to right/left if needed
            preferred_starts = []

            # If offscreen, bias the label inward so it stays visible
            if off_dir < 0:
                # left edge: place to the right of the marker
                preferred_starts = [col + 1, col, col + 2, col - len(txt)]
            elif off_dir > 0:
                # right edge: place to the left of the marker
                preferred_starts = [col - len(txt), col - len(txt) - 1, col - len(txt) - 2, col]
            else:
                # in-range: try centred then near
                preferred_starts = [
                    col - (len(txt) // 2),
                    col + 1,
                    col - len(txt),
                    col - (len(txt) // 2) - 1,
                    col - (len(txt) // 2) + 1,
                ]

            placed = False
            for start in preferred_starts:
                start = max(0, min(width - len(txt), start))
                if can_place(start, txt):
                    place_text(start, txt)
                    placed = True
                    break

            # If we can't place (too crowded), just skip that label.
            # The ▲ still shows the peak exists.

        lines.append("".join(label_line))
        for row in range(height):
            threshold = max_db - (row * db_range / height)
            line = ""
            for i, db in enumerate(display_bins):
                if db < min_db:
                    db = min_db
                elif db > max_db:
                    db = max_db
                char = " "
                if have_spectrum:
                    if db >= threshold:
                        char = "█"
                    elif db >= threshold - (db_range / height / 2):
                        char = "▄"

                ghost_char = " "
                if char == " " and hold_bins and hold_bins[i] is not None:
                    if hold_bins[i] >= threshold:
                        ghost_char = "·"

                in_if = half_if_bw and abs(i - mask_center) <= half_if_bw
                in_bw = half_bw and abs(i - mask_center) <= half_bw

                # Highlight current cursor position as a single bin
                if i == cursor_pos:
                    line += cursor_marker
                elif char.strip():
                    if in_if:
                        line += f"[{IF_BG}]{char}[/{IF_BG}]"
                    elif in_bw:
                        line += f"[{BW_BG}]{char}[/{BW_BG}]"
                    else:
                        line += char
                else:
                    if in_if:
                        if ghost_char.strip():
                            line += "[grey35 on grey30]·[/grey35 on grey30]"
                        else:
                            line += f"[{IF_BG}] [/{IF_BG}]"
                    elif in_bw:
                        if ghost_char.strip():
                            line += "[grey35 on grey35]·[/grey35 on grey35]"
                        else:
                            line += f"[{BW_BG}] [/{BW_BG}]"
                    else:
                        if ghost_char.strip():
                            line += "[grey35]·[/grey35]"
                        else:
                            line += " "
            lines.append(line)
        
        # Add detailed frequency labels
        major_tick_mhz = 0.5
        minor_tick_mhz = 0.1

        def freq_to_col(freq):
            return int((freq - overview_start) / overview_span * width)

        tick_line = [" "] * width
        f = overview_start
        while f <= overview_end:
            col = freq_to_col(f)
            if 0 <= col < width:
                if abs((f / major_tick_mhz) - round(f / major_tick_mhz)) < 1e-6:
                    tick_line[col] = "┼"
                else:
                    tick_line[col] = "┬"
            f += minor_tick_mhz

        lines.append("".join(tick_line))

        labels = [" "] * width

        def put_label(text, pos):
            start = max(0, pos - len(text) // 2)
            end = min(width, start + len(text))
            for i, c in enumerate(text[:end - start]):
                labels[start + i] = c

        f = overview_start
        while f <= overview_end:
            if abs((f / major_tick_mhz) - round(f / major_tick_mhz)) < 1e-6:
                col = freq_to_col(f)
                put_label(f"{f:.1f}", col)
            f += major_tick_mhz

        put_label(f"{overview_start:.1f}", 1)
        put_label(f"{overview_end:.1f}", width - 4)
        put_label(f"{display_freq:.1f}", cursor_pos)

        lines.append("".join(labels))
        
        return "\n".join(lines)

    def _update_audio_offset(self):
        if self.center_mhz is None:
            self.audio_offset_hz = 0.0
        else:
            self.audio_offset_hz = (self.audio_target_mhz - self.center_mhz) * 1e6

    def _build_fir_taps(self, cutoff_hz, num_taps=101):
        if cutoff_hz <= 0:
            return None
        fc = cutoff_hz / (self.sample_rate / 2.0)
        fc = min(0.95, max(0.001, fc))
        n = np.arange(num_taps) - (num_taps - 1) / 2.0
        taps = fc * np.sinc(fc * n)
        taps *= np.hamming(num_taps)
        taps = taps / np.sum(taps)
        return taps.astype(np.float32)

    def _update_audio_filter(self):
        if self.modulation == "WFM":
            cutoff = 16000.0
        elif self.modulation == "AM":
            cutoff = 5000.0
        elif self.modulation == "DSB":
            cutoff = 4500.0
        elif self.modulation in ("USB", "LSB"):
            cutoff = 2800.0
        elif self.modulation == "CW":
            cutoff = 900.0
        else:
            cutoff = min((self.if_bw_khz * 1000.0) / 2.0, 12000.0)

        if cutoff != self._audio_taps_cutoff:
            self._audio_taps = self._build_fir_taps(cutoff, num_taps=101)
            self._audio_taps_cutoff = cutoff
            self._audio_fir_state = None

    def _fir_filter(self, x):
        taps = self._audio_taps
        if taps is None or len(x) == 0:
            return x
        if self._audio_fir_state is None:
            self._audio_fir_state = np.zeros(len(taps) - 1, dtype=np.float32)
        x = np.concatenate([self._audio_fir_state, x])
        y = np.convolve(x, taps, mode="valid")
        self._audio_fir_state = x[-(len(taps) - 1):]
        return y

    def _apply_deemph(self, x):
        if self.modulation != "WFM" or len(x) == 0:
            return x
        tau = 75e-6
        alpha = np.exp(-1.0 / (self.audio_rate * tau))
        y = np.empty_like(x)
        prev = self._audio_deemph_state
        for i in range(len(x)):
            prev = alpha * prev + (1 - alpha) * x[i]
            y[i] = prev
        self._audio_deemph_state = prev
        return y

    def _ssb_bfo_hz(self, mode):
        if mode == "CW":
            return 700.0
        return 0.0

    def fine_tune_step_mhz(self):
        if self.modulation in ("USB", "LSB"):
            return 0.001
        if self.modulation == "CW":
            return 0.0005
        if self.modulation in ("AM", "DSB", "NFM"):
            return 0.01
        return 0.1

    def _audio_demod_loop(self):
        try:
            self._audio_demod_body()
        except Exception as exc:  # surfaced to the user, not swallowed
            self.audio_error = f"Audio decoder stopped: {exc}"[:80]
            log(f"SDR: {self.audio_error}")
            self.audio_stop.set()
        finally:
            self.demod_running = False

    def _squelch_update(self, pwr, now=None):
        """Squelch decision with hysteresis and a short hang time."""
        if self.squelch_level <= 0:
            self.squelch_open = True
            return True
        now = time.time() if now is None else now
        base = self.audio_noise_est if self.audio_noise_est is not None else pwr
        open_at = base + (self.squelch_level / 3.0)
        close_at = open_at - self.squelch_hysteresis_db
        if pwr > open_at or (self.squelch_open and pwr > close_at):
            self.squelch_open = True
            self._squelch_hang_until = now + self.squelch_hang_s
        elif now < self._squelch_hang_until:
            self.squelch_open = True
        else:
            self.squelch_open = False
        return self.squelch_open

    def snr_db(self):
        """Tuned signal level above the noise floor of the current spectrum."""
        if self.s_meter_db is None or not self.spectrum:
            return None
        floor = sorted(self.spectrum)[int(len(self.spectrum) * 0.2)]
        return self.s_meter_db - floor

    def _check_audio_health(self):
        """Turn audio off cleanly if the decoder thread or paplay has died."""
        if not self.audio_enabled:
            return
        thread_dead = self.audio_thread is None or not self.audio_thread.is_alive()
        player_dead = self.paplay_proc is not None and self.paplay_proc.poll() is not None
        if not (thread_dead or player_dead):
            return
        if self.audio_error is None:
            self.audio_error = "Audio output stopped (paplay exited)" if player_dead else "Audio decoder stopped"
        log(f"SDR: {self.audio_error}")
        self.stop_audio()

    def _audio_demod_body(self):
        self.demod_running = True
        self._update_audio_filter()
        decim = self.audio_decim

        while not self.audio_stop.is_set():
            with self.iq_cond:
                if not self.iq_ring:
                    self.iq_cond.wait(timeout=0.1)
                    if self.audio_stop.is_set():
                        break
                    if not self.iq_ring:
                        continue
                chunk = self.iq_ring.popleft()

            if not chunk:
                continue

            iq_u8 = np.frombuffer(chunk, dtype=np.uint8).astype(np.float32)
            iq_u8 -= 127.5
            i = iq_u8[0::2]
            q = iq_u8[1::2]
            x = i + 1j * q
            if len(x) == 0:
                continue

            offset_hz = self.audio_offset_hz
            w = 2 * np.pi * offset_hz / self.sample_rate
            if w != 0.0:
                n = np.arange(len(x), dtype=np.float32)
                phase0 = self.audio_phase
                osc = np.exp(-1j * (phase0 + w * n))
                x = x * osc
                self.audio_phase = (phase0 + w * len(x)) % (2 * np.pi)
            else:
                self.audio_phase = self.audio_phase % (2 * np.pi)

            pwr = 10 * np.log10(np.mean(np.abs(x) ** 2) + 1e-12)
            if self.audio_noise_est is None:
                self.audio_noise_est = pwr
            else:
                if pwr < self.audio_noise_est + 3:
                    self.audio_noise_est = 0.95 * self.audio_noise_est + 0.05 * pwr
                elif self.squelch_level == 0 or not self.squelch_open:
                    self.audio_noise_est = 0.99 * self.audio_noise_est + 0.01 * pwr
                # else: a signal is holding the squelch open, so keep the floor
                # where it was rather than letting the signal drag it up and
                # chop the transmission off after a second or so.

            squelch_open = self._squelch_update(pwr)

            mode = self.modulation
            if mode in ("FM", "WFM", "NFM"):
                if self._audio_last_sample is None:
                    y = x
                else:
                    y = np.concatenate(([self._audio_last_sample], x))
                prod = y[1:] * np.conj(y[:-1])
                audio = np.angle(prod).astype(np.float32)
                self._audio_last_sample = x[-1]
            elif mode in ("USB", "LSB", "CW"):
                bfo_hz = self._ssb_bfo_hz(mode)
                if bfo_hz:
                    n = np.arange(len(x), dtype=np.float32)
                    osc = np.exp(1j * (2 * np.pi * bfo_hz / self.sample_rate) * n)
                    x = x * osc
                audio = np.real(x).astype(np.float32)
                audio = audio - np.mean(audio)
                self._audio_last_sample = x[-1]
            elif mode == "DSB":
                audio = np.real(x).astype(np.float32)
                audio = audio - np.mean(audio)
                self._audio_last_sample = x[-1]
            else:
                audio = np.abs(x).astype(np.float32)
                audio = audio - np.mean(audio)
                self._audio_last_sample = x[-1]

            self._update_audio_filter()
            audio = self._fir_filter(audio)
            if decim > 1:
                audio = audio[::decim]
            if mode == "WFM":
                audio = self._apply_deemph(audio)

            if len(audio) == 0:
                continue

            if not squelch_open:
                audio = np.zeros_like(audio)

            gain = 10 ** (self.audio_gain / 20.0)
            audio = np.clip(audio * gain, -1.0, 1.0)
            pcm = (audio * 32767.0).astype(np.int16).tobytes()

            if self.paplay_proc and self.paplay_proc.stdin:
                try:
                    self.paplay_proc.stdin.write(pcm)
                except (BrokenPipeError, OSError):
                    self.audio_stop.set()
                    break

        self.demod_running = False
    
    def start_audio(self, freq_mhz=None):
        """Start FM audio demodulation and route to PulseAudio"""
        if not self.active:
            return
        if self.audio_enabled:
            return

        if freq_mhz is None:
            freq_mhz = self.freq_mhz

        self.audio_target_mhz = freq_mhz
        self._update_audio_offset()
        self._update_audio_filter()
        self.audio_stop.clear()
        self.audio_phase = 0.0
        self.audio_noise_est = None
        self._audio_last_sample = None
        self._audio_fir_state = None
        self._audio_deemph_state = 0.0
        self._audio_buf.clear()
        with self.iq_cond:
            self.iq_ring.clear()
            self.iq_cond.notify_all()

        if not (self.panadapter_proc and self.panadapter_proc.poll() is None):
            self.start_panadapter()
            self.prime_spectrum()

        try:
            self.audio_error = None
            self.paplay_proc = subprocess.Popen(
                [
                    "paplay",
                    "--format=s16le",
                    "--rate=48000",
                    "--channels=1",
                    "--raw",
                ],
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                preexec_fn=_die_with_parent,
            )

            self.audio_enabled = True
            self.audio_thread = threading.Thread(target=self._audio_demod_loop, daemon=True)
            self.audio_thread.start()
            self.dirty = True
            debug(f"SDR audio started at {freq_mhz} MHz")
            log(f"SDR audio: {freq_mhz} MHz → PulseAudio (gain {self.audio_gain} dB)")
        except Exception as e:
            self.audio_enabled = False
            if isinstance(e, FileNotFoundError):
                self.audio_error = "paplay not found (install pulseaudio-utils)"
            else:
                self.audio_error = f"Audio start failed: {e}"[:80]
            debug(f"SDR audio start failed: {e}")
            log(f"SDR audio failed: {e}")
            self.paplay_proc = None
            self.audio_thread = None
    
    def stop_audio(self):
        """Stop FM audio demodulation"""
        if not self.audio_enabled:
            return

        self.audio_enabled = False
        self.audio_stop.set()
        with self.iq_cond:
            self.iq_ring.clear()
            self.iq_cond.notify_all()

        if self.audio_thread:
            self.audio_thread.join(timeout=1.0)
            self.audio_thread = None

        if self.paplay_proc:
            try:
                terminate_process(self.paplay_proc)
            finally:
                self.paplay_proc = None

        self.dirty = True
        debug("SDR audio stopped")
        log("SDR audio stopped")

    def _rtl_biast_cmds(self, enabled):
        device = str(self.rtlsdr_device) if self.rtlsdr_device is not None else "0"
        value = "1" if enabled else "0"
        primary = ["sudo", "-n", "rtl_biast", "-d", device, "-b", value]
        fallback = ["sudo", "-n", "rtl_biast", "-b", value]
        direct_primary = ["rtl_biast", "-d", device, "-b", value]
        direct_fallback = ["rtl_biast", "-b", value]
        return [primary, fallback, direct_primary, direct_fallback]

    def set_bias_t(self, enabled):
        """Enable or disable RTL-SDR bias-t"""
        if not self.active:
            return False

        was_panadapter_running = bool(self.panadapter_proc and self.panadapter_proc.poll() is None)
        if was_panadapter_running:
            debug("SDR: pausing panadapter for bias-t toggle")
            self.stop_panadapter()

        try:
            last_error = ""
            for cmd in self._rtl_biast_cmds(enabled):
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=1,
                )
                if power.DEBUG:
                    debug(f"SDR: bias-t cmd: {' '.join(cmd)}")
                    debug(f"SDR: bias-t rc={result.returncode}")
                    out = (result.stderr or result.stdout or "").strip()
                    if out:
                        debug(f"SDR: bias-t out: {out[:120]}")
                if result.returncode == 0:
                    self.bias_t_enabled = enabled
                    self.last_bias_t_error = None
                    debug(f"SDR: bias-t {'enabled' if enabled else 'disabled'}")
                    log(f"SDR: bias-t {'ON' if enabled else 'OFF'}")
                    if was_panadapter_running:
                        self.start_panadapter()
                        self.prime_spectrum()
                    return True
                last_error = (result.stderr or result.stdout or "").strip()

            self.last_bias_t_error = last_error or "unknown error"
            debug(f"SDR: bias-t toggle failed: {self.last_bias_t_error[:120]}")
            log(f"SDR: bias-t toggle failed - {self.last_bias_t_error[:60]}")
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self.last_bias_t_error = str(e)
            debug(f"SDR: bias-t toggle failed: {e}")
            log(f"SDR: bias-t toggle failed - {str(e)[:60]}")
            return False
        finally:
            if was_panadapter_running and not self.panadapter_proc:
                self.start_panadapter()
                self.prime_spectrum()
    
    def get_status(self):
        """Return status string"""
        if not self.active:
            return self.status_reason

        if self.link_state != "ok" and self.last_error:
            return self.last_error

        audio_status = f" 🔊 Sq:{self.squelch_level}" if self.audio_enabled else ""

        if not self.scan_enabled:
            return f"Ready (not scanning){audio_status}"

        if self.seek_active:
            direction = "→" if self.seek_dir > 0 else "←"
            return f"Seeking {direction} {self.freq_mhz:.1f} MHz...{audio_status}"

        if self.changing_freq or self.pending_freq is not None:
            display_freq = self.pending_freq if self.pending_freq is not None else self.freq_mhz
            return f"Tuning {display_freq:.1f} MHz | BW {self.bandwidth_mhz:.1f} MHz...{audio_status}"

        if self.s_meter_db is not None:
            return f"{self.modulation} | {self.freq_mhz:.2f} MHz | BW {self.bandwidth_mhz:.1f} MHz{audio_status}"

        return f"{self.modulation} | {self.freq_mhz:.2f} MHz | BW {self.bandwidth_mhz:.1f} MHz{audio_status}"
