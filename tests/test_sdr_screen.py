"""The SDR tab's screen: layout sums, theme colours, wording, squelch, and the seek and scan logic. No Qt."""
import inspect
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

try:
    import numpy as np
    from ops_gui import sdr_panel as sp
except ImportError:                         # the panel's helpers need numpy, as the engine does
    np = sp = None

try:
    from .sdr_helpers import make_sdr
except ImportError:
    from sdr_helpers import make_sdr

SIZES = ((1280, 720), (1250, 590), (900, 480), (880, 360))
RATE = 2_400_000


def overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def spectrum(center, signals=(), n=2048, seed=1, spike=0.0):
    """A made-up FFT in dB: noise with the real spread of a single FFT, plus (MHz, width kHz, dB) signals."""
    rng = np.random.default_rng(seed)
    power = rng.exponential(1.0, n)
    bin_mhz = RATE / 1e6 / n
    for mhz, width_khz, db in signals:
        i = int(round((mhz - center) / bin_mhz)) + n // 2
        half = max(1, int(width_khz / 1000.0 / bin_mhz / 2))
        lo, hi = max(0, i - half), min(n, i + half + 1)
        if hi > lo:
            power[lo:hi] += 10 ** (db / 10.0)
    if spike:
        power[n // 2] += 10 ** (spike / 10.0)
    return (10 * np.log10(power)).tolist()


class Radio:
    """Stands in for the engine and the air: moves its window by the engine's own rule."""

    def __init__(self, center, signals):
        self.center = center
        self.tuned = center
        self.signals = signals
        self.retunes = 0
        self.frame = 0

    def tune(self, mhz):
        self.tuned = mhz
        if sp.needs_retune(mhz, self.center, RATE):
            self.center = mhz
            self.retunes += 1

    def run(self, seeker, start=0.0, seconds=120.0, until_tunes=None):
        """Tick the seeker ten times a second. Returns (frequencies it tuned to on a signal, messages)."""
        hits, messages, now = [], [], start
        while seeker.active and now < start + seconds:
            now += 0.1
            self.frame += 1
            action = seeker.step(now, spectrum(self.center, self.signals, seed=self.frame), self.center, False)
            if action:
                mhz, message = action
                if mhz is not None:
                    self.tune(mhz)
                if message:
                    messages.append(message)
                    if mhz is not None:
                        hits.append(mhz)
                        if until_tunes and len(hits) >= until_tunes:
                            break
        self.now = now
        return hits, messages


@unittest.skipIf(sp is None, "numpy not installed")
class LayoutTests(unittest.TestCase):
    def test_everything_fits_inside_the_page_at_every_size(self):
        for w, h in SIZES:
            for x, y, rw, rh in sp.all_rects(sp.layout(w, h)):
                self.assertGreaterEqual(x, 0, (w, h))
                self.assertGreaterEqual(y, 0, (w, h))
                self.assertLessEqual(x + rw, w, (w, h))
                self.assertLessEqual(y + rh, h, (w, h))
                self.assertGreater(rw, 0)
                self.assertGreater(rh, 0)

    def test_no_two_controls_overlap(self):
        for w, h in SIZES:
            rects = sp.all_rects(sp.layout(w, h))
            for i, a in enumerate(rects):
                for b in rects[i + 1:]:
                    self.assertFalse(overlap(a, b), (w, h, a, b))

    def test_keys_are_touch_sized_and_all_the_same_height(self):
        self.assertEqual(sp.layout(1280, 720)["key_h"], 48)
        self.assertEqual(sp.layout(1250, 590)["key_h"], 48)
        self.assertEqual(sp.layout(900, 480)["key_h"], 40)
        for w, h in SIZES:
            lay = sp.layout(w, h)
            keys = lay["modes"] + lay["steps"] + [lay[k] for k in ("scan", "entry", "band", "cb", "preset", "upconv",
                                                                   "vol_down", "vol_up", "sq_down", "sq_up")]
            self.assertEqual({k[3] for k in keys}, {lay["key_h"]})
            self.assertGreaterEqual(min(k[2] for k in keys), 56)

    def test_the_mode_seek_and_scan_keys_are_one_even_row(self):
        for w, h in SIZES:
            lay = sp.layout(w, h)
            row = lay["modes"] + lay["steps"] + [lay["scan"]]
            self.assertEqual(len(row), 12)
            self.assertEqual(len({r[1] for r in row}), 1)
            widths = [r[2] for r in row]
            self.assertLessEqual(max(widths) - min(widths), 1)
            self.assertEqual(row[0][0], sp.MARGIN)
            self.assertEqual(row[-1][0] + row[-1][2], w - sp.MARGIN)

    def test_the_spectrum_gets_all_the_height_the_keys_do_not_need(self):
        lay = sp.layout(1250, 590)
        self.assertEqual(lay["view"][1], sp.MARGIN)
        self.assertEqual(lay["view"][1] + lay["view"][3] + sp.GAP, lay["entry"][1])
        self.assertGreater(lay["view"][3], 590 * 0.6)
        self.assertGreaterEqual(sp.layout(880, 360)["view"][3], 150)

    def test_the_switch_size_matches_the_shared_slide_switch(self):
        from ops_gui import slideswitch
        self.assertEqual((sp.SWITCH_W, sp.SWITCH_H), (slideswitch.WIDTH, slideswitch.HEIGHT))
        for w, h in SIZES:
            lay = sp.layout(w, h)
            for name in ("listen", "squelch", "bias"):
                self.assertEqual(lay[name]["switch"][2:], (sp.SWITCH_W, sp.SWITCH_H))
                self.assertLessEqual(lay[name]["switch"][3], lay["key_h"])

    def test_every_caption_and_hint_fits_its_box(self):
        for w, h in SIZES:
            lay = sp.layout(w, h)
            for name, options in sp.CAPTIONS.items():
                text = sp.caption(options, lay[name]["label"][2])
                self.assertLessEqual(len(text) * 8, lay[name]["label"][2], (w, name))
            self.assertLessEqual(len(sp.PLACEHOLDER) * 8, lay["entry"][2] - 16, (w, h))
            self.assertLessEqual(len(sp.BIAS_WARNING) * 8, lay["hint"][2], (w, h))
            self.assertLessEqual(len("49 dB") * 12, lay["vol_value"][2])
            for text in ("VOL -", "VOL +", "SQ -", "SQ +", "SCAN", "<<", "WFM"):
                self.assertLessEqual(len(text) * 8, lay["scan"][2] - 16)

    def test_the_full_word_is_used_when_there_is_room_and_the_short_one_when_not(self):
        self.assertEqual(sp.caption(("Squelch", "Sql"), 60), "Squelch")
        self.assertEqual(sp.caption(("Squelch", "Sql"), 50), "Sql")
        self.assertEqual(sp.caption(("Squelch", "Sql"), 5), "Sql")
        self.assertEqual(sp.caption(sp.CAPTIONS["squelch"], sp.layout(1280, 720)["squelch"]["label"][2]), "Squelch")


@unittest.skipIf(sp is None, "numpy not installed")
class ViewLayoutTests(unittest.TestCase):
    def views(self):
        return [sp.layout(w, h)["view"][2:] for w, h in SIZES]

    def test_the_waterfall_fills_the_widget_to_the_bottom_edge(self):
        for w, h in self.views():
            lay = sp.view_layout(w, h)
            x, y, ww, wh = lay["waterfall"]
            self.assertEqual((x, ww), (0, w))
            self.assertEqual(y + wh, h)
            self.assertEqual(y, lay["plot_h"] + lay["axis"][3])
            self.assertGreaterEqual(wh, 60)

    def test_the_readout_sits_inside_the_trace_area_and_clear_of_the_meter(self):
        for w, h in self.views():
            lay = sp.view_layout(w, h, 12, 80)
            sx, sy, sw, sh = lay["strip"]
            self.assertLessEqual(sy + sh, lay["plot_h"], (w, h))
            self.assertLessEqual(sx + sw, w)
            self.assertIsNotNone(lay["meter"])
            self.assertFalse(overlap(lay["strip"], lay["meter"]))
            self.assertLessEqual(lay["meter"][0] + lay["meter"][2], w)
            for name in ("freq", "unit", "status"):
                r = lay[name]
                self.assertGreaterEqual(r[0], sx)
                self.assertLessEqual(r[0] + r[2], sx + sw, name)
                self.assertLessEqual(r[1] + r[3], sy + sh, name)

    def test_the_frequency_is_big_on_the_kiosk_screen_and_smaller_on_a_short_one(self):
        w, h = sp.layout(1250, 590)["view"][2:]
        self.assertEqual(sp.view_layout(w, h)["big"], 64)
        w, h = sp.layout(880, 360)["view"][2:]
        self.assertEqual(sp.view_layout(w, h)["big"], 32)

    def test_the_longest_frequency_fits_the_strip(self):
        for w, h in self.views():
            lay = sp.view_layout(w, h, len(sp.format_freq(1766.0)), 0)
            self.assertLessEqual(len(sp.format_freq(1766.0)) * (lay["big"] // 2), lay["freq"][2])

    def test_the_status_is_cut_to_what_the_strip_can_show(self):
        lay = sp.view_layout(864, 172, 12, 400)
        self.assertLessEqual(len(sp.fit("x" * 400, lay["status_chars"])) * 8, lay["status"][2])
        self.assertTrue(sp.fit("x" * 400, 10).endswith("~"))
        self.assertEqual(sp.fit("short", 10), "short")

    def test_a_new_waterfall_is_stretched_over_its_area_instead_of_leaving_a_gap(self):
        self.assertEqual(sp.rows_to_draw(0, 200), 0)
        self.assertEqual(sp.rows_to_draw(1, 200), 1)
        self.assertEqual(sp.rows_to_draw(7, 200), 7)
        self.assertEqual(sp.rows_to_draw(500, 200), 50)
        self.assertEqual(sp.waterfall_rows(40), 20)
        self.assertEqual(sp.waterfall_rows(5000), sp.WATERFALL_ROWS)

    def test_the_meter_lights_in_step_with_the_signal(self):
        self.assertEqual(sp.meter_segments(0.0), 0)
        self.assertEqual(sp.meter_segments(0.5), 10)
        self.assertEqual(sp.meter_segments(7), sp.METER_SEGMENTS)


@unittest.skipIf(sp is None, "numpy not installed")
class ColourTests(unittest.TestCase):
    def test_text_drawn_on_the_spectrum_is_readable_on_both_themes(self):
        for mode in ("dark", "light"):
            col = sp.colours(mode)
            self.assertGreaterEqual(sp.contrast(col["text"], col["base"]), 7.0, mode)
            self.assertGreaterEqual(sp.contrast(col["muted"], col["base"]), 4.5, mode)
            self.assertGreaterEqual(sp.contrast(col["tuned"], col["base"]), 7.0, mode)

    def test_the_trace_peaks_and_meter_stand_out_on_both_themes(self):
        for mode in ("dark", "light"):
            col = sp.colours(mode)
            for name in ("trace", "peak", "meter_on"):
                self.assertGreaterEqual(sp.contrast(col[name], col["base"]), 3.0, (mode, name))
            self.assertGreater(sp.contrast(col["meter_on"], col["meter_off"]), 2.0, mode)

    def test_the_two_themes_really_differ_and_come_from_the_shared_tokens(self):
        from ops_gui import style
        dark, light = sp.colours("dark"), sp.colours("light")
        self.assertNotEqual(dark["base"], light["base"])
        self.assertEqual(dark["text"], style.TOKENS["dark"]["text"])
        self.assertEqual(light["text"], style.TOKENS["light"]["text"])
        self.assertEqual(light["muted"], style.MUTED["light"])
        self.assertEqual(sp.colours("nonsense"), dark)

    def test_the_marker_over_the_waterfall_is_pale_because_the_waterfall_is_always_dark(self):
        quiet = "#%02x%02x%02x" % tuple(int(v) for v in sp.LUT[0])
        for mode in ("dark", "light"):
            self.assertGreaterEqual(sp.contrast(sp.colours(mode)["waterfall_marker"], quiet), 7.0)

    def test_the_page_writes_no_fixed_colours_of_its_own(self):
        import re
        body = inspect.getsource(sp.build_panel)
        self.assertEqual(set(re.findall(r"#[0-9a-fA-F]{6}", body)), {"#9e9e9e"})    # the grey the app swaps per theme

    def test_the_hint_in_the_frequency_box_uses_the_helper_grey_for_the_theme(self):
        from ops_gui import style
        body = inspect.getsource(sp.build_panel)
        self.assertIn("placeholder-text-color: #9e9e9e", body)
        self.assertIn("PlaceholderText", body)
        self.assertIn("#5f6368", style.muted_sheet("placeholder-text-color: #9e9e9e;", "light"))


@unittest.skipIf(sp is None, "numpy not installed")
class WordingTests(unittest.TestCase):
    def test_the_status_line_says_what_the_radio_is_set_to(self):
        self.assertEqual(sp.status_line("WFM", 200), "WFM | IF 200 kHz | Muted | Sq off")
        self.assertEqual(sp.status_line("NFM", 12, listening=True, squelch_level=30, squelch_open=False),
                         "NFM | IF 12 kHz | Listening | Sq 30 shut")
        self.assertIn("Sq 30 open", sp.status_line("NFM", 12, listening=True, squelch_level=30))

    def test_squelch_open_or_shut_is_only_claimed_while_listening(self):
        line = sp.status_line("NFM", 12, listening=False, squelch_level=30, squelch_open=True)
        self.assertIn("Sq 30", line)
        self.assertNotIn("open", line)
        self.assertNotIn("shut", line)

    def test_bias_and_upconverter_show_only_when_they_are_on(self):
        self.assertNotIn("Bias", sp.status_line("AM", 10))
        self.assertIn("Bias-T ON", sp.status_line("AM", 10, bias_on=True))
        self.assertIn("Upconv +125 MHz", sp.status_line("LSB", 3, upconverter_mhz=125.0))
        self.assertIn("Seeking up", sp.status_line("AM", 10, seeking="Seeking up"))

    def test_a_problem_replaces_the_status(self):
        self.assertEqual(sp.status_line("AM", 10, problem="Dongle dropped out"), "Dongle dropped out")

    def test_the_status_line_is_plain_text_the_pixel_font_can_draw(self):
        line = sp.status_line("NFM", 12, True, 30, True, True, 125.0, "Scanning up")
        self.assertTrue(line.isascii())
        self.assertLessEqual(len(line), 90)

    def test_the_bias_warning_is_plain_about_the_voltage(self):
        self.assertIn("voltage on the antenna lead", sp.BIAS_WARNING)
        self.assertIn("off unless", sp.BIAS_WARNING)
        self.assertIn("never switched on for you", sp.BIAS_TIP)

    def test_bias_results_say_what_really_happened(self):
        self.assertIn("ON", sp.bias_result_text(True, True))
        self.assertEqual(sp.bias_result_text(False, True), "Bias-T is off.")
        self.assertEqual(sp.bias_result_text(True, False, "usb_claim_interface error -6\n"),
                         "Could not switch Bias-T on: usb_claim_interface error -6")
        self.assertEqual(sp.bias_result_text(False, False, None), "Could not switch Bias-T off: unknown error")

    def test_signal_wording(self):
        self.assertEqual(sp.signal_text(None), "-- dB over noise")
        self.assertEqual(sp.signal_text(23.4), "+23 dB over noise")

    def test_upconverter_wording(self):
        self.assertEqual(sp.upconverter_text(0), "Upconverter off.")
        self.assertIn("+125 MHz", sp.upconverter_text(125.0))


@unittest.skipIf(sp is None, "numpy not installed")
class SquelchAndVolumeTests(unittest.TestCase):
    def test_switching_squelch_off_stores_zero_and_on_stores_the_level(self):
        self.assertEqual(sp.squelch_engine_level(False, 30), 0)
        self.assertEqual(sp.squelch_engine_level(True, 30), 30)
        self.assertEqual(sp.squelch_engine_level(True, 0), sp.SQUELCH_MIN)
        self.assertEqual(sp.squelch_engine_level(True, 999), sp.SQUELCH_MAX)

    def test_the_level_is_remembered_while_the_switch_is_off(self):
        self.assertEqual(sp.squelch_from_engine(0, 45), (False, 45))
        self.assertEqual(sp.squelch_from_engine(20, 45), (True, 20))

    def test_the_level_steps_by_five_between_its_limits(self):
        self.assertEqual(sp.squelch_step(30, 1), 35)
        self.assertEqual(sp.squelch_step(30, -1), 25)
        self.assertEqual(sp.squelch_step(sp.SQUELCH_MAX, 1), sp.SQUELCH_MAX)
        self.assertEqual(sp.squelch_step(sp.SQUELCH_MIN, -1), sp.SQUELCH_MIN)

    def test_set_squelch_writes_the_engine_and_asks_for_a_redraw(self):
        e = make_sdr()
        e.dirty = False
        sp.set_squelch(e, True, 35)
        self.assertEqual(e.squelch_level, 35)
        self.assertTrue(e.dirty)
        sp.set_squelch(e, False, 35)
        self.assertEqual(e.squelch_level, 0)

    def test_volume_walks_the_ladder_and_stops_at_its_ends(self):
        self.assertEqual(sp.volume_step(20, 1), 25)
        self.assertEqual(sp.volume_step(20, -1), 15)
        self.assertEqual(sp.volume_step(45, 1), 49)
        self.assertEqual(sp.volume_step(49, 1), 49)
        self.assertEqual(sp.volume_step(49, -1), 45)
        self.assertEqual(sp.volume_step(0, -1), 0)
        self.assertEqual(sp.volume_step(22, 1), 25)

    def test_the_engine_starts_inside_the_volume_ladder(self):
        self.assertIn(make_sdr().audio_gain, sp.VOLUME_LEVELS)


@unittest.skipIf(sp is None, "numpy not installed")
class MeasuringTests(unittest.TestCase):
    def test_noise_alone_is_never_called_a_signal(self):
        for seed in range(30):
            spec = spectrum(100.0, seed=seed)
            self.assertIsNone(sp.next_signal(spec, 100.0, RATE, 1, 100.0), seed)
            self.assertIsNone(sp.next_signal(spec, 100.0, RATE, -1, 100.0), seed)

    def test_the_spike_a_dongle_leaves_at_the_centre_is_not_a_signal(self):
        for seed in range(10):
            spec = spectrum(100.0, seed=seed, spike=25.0)
            self.assertIsNone(sp.next_signal(spec, 100.0, RATE, 1, None), seed)

    def test_the_next_signal_up_and_down_is_found_at_its_middle(self):
        spec = spectrum(100.0, [(100.5, 180, 25), (99.4, 180, 25)])
        up = sp.next_signal(spec, 100.0, RATE, 1, 100.0)
        down = sp.next_signal(spec, 100.0, RATE, -1, 100.0)
        self.assertAlmostEqual(up[0], 100.5, delta=0.01)
        self.assertAlmostEqual(down[0], 99.4, delta=0.01)
        self.assertGreater(up[1], 15)

    def test_a_narrow_signal_is_found_too(self):
        spec = spectrum(145.5, [(145.8, 10, 22)])
        self.assertAlmostEqual(sp.next_signal(spec, 145.5, RATE, 1, 145.5)[0], 145.8, delta=0.006)

    def test_seeking_from_a_station_leaves_it_and_finds_the_next_one(self):
        spec = spectrum(100.0, [(100.0, 180, 25), (100.6, 180, 25)])
        self.assertAlmostEqual(sp.next_signal(spec, 100.0, RATE, 1, 100.0)[0], 100.6, delta=0.01)
        self.assertIsNone(sp.next_signal(spec, 100.0, RATE, 1, 100.6))

    def test_it_works_when_the_window_is_not_centred_on_the_tuned_frequency(self):
        spec = spectrum(100.0, [(100.3, 180, 25), (100.9, 180, 25)])
        self.assertAlmostEqual(sp.next_signal(spec, 100.0, RATE, 1, 100.3)[0], 100.9, delta=0.01)
        self.assertAlmostEqual(sp.next_signal(spec, 100.0, RATE, -1, 100.9)[0], 100.3, delta=0.01)

    def test_signals_outside_the_band_are_not_stopped_on(self):
        spec = spectrum(100.0, [(100.5, 180, 25)])
        self.assertIsNone(sp.next_signal(spec, 100.0, RATE, 1, 100.0, low=99.0, high=100.3))
        self.assertIsNotNone(sp.next_signal(spec, 100.0, RATE, 1, 100.0, low=99.0, high=101.0))

    def test_a_signal_running_off_the_far_edge_is_left_for_the_next_window(self):
        spec = spectrum(100.0, [(101.1, 200, 25)])
        self.assertIsNone(sp.next_signal(spec, 100.0, RATE, 1, 100.0))

    def test_starting_past_the_window_or_with_no_spectrum_finds_nothing(self):
        self.assertIsNone(sp.next_signal([], 100.0, RATE, 1, 100.0))
        self.assertIsNone(sp.next_signal(spectrum(100.0, [(100.5, 180, 25)]), 100.0, RATE, 1, 105.0))
        self.assertIsNone(sp.next_signal(spectrum(100.0), None, RATE, 1, 100.0))

    def test_the_meter_reads_the_tuned_frequency_not_the_middle_of_the_window(self):
        spec = spectrum(100.0, [(100.4, 180, 30)])
        self.assertGreater(sp.signal_db(spec, 100.0, RATE, 100.4, 200), 20)
        self.assertLess(sp.signal_db(spec, 100.0, RATE, 100.0, 200), 5)

    def test_the_meter_says_nothing_when_nothing_was_measured(self):
        self.assertIsNone(sp.signal_db([], 100.0, RATE, 100.0, 200))
        self.assertIsNone(sp.signal_db(spectrum(100.0), None, RATE, 100.0, 200))
        self.assertIsNone(sp.signal_db(spectrum(100.0), 100.0, RATE, 110.0, 200))

    def test_found_frequencies_go_on_the_grid_the_band_uses(self):
        self.assertEqual(sp.tidy_hit(98.7934, "WFM"), 98.8)
        self.assertEqual(sp.tidy_hit(145.5004, "NFM"), 145.5)
        channels = [{"channel": 1, "freq_mhz": 27.60125}, {"channel": 2, "freq_mhz": 27.61125}]
        self.assertEqual(sp.tidy_hit(27.6032, "NFM", channels), 27.60125)
        self.assertEqual(sp.tidy_hit(27.7, "NFM", channels), 27.7)

    def test_a_seek_stays_in_the_band_you_are_in(self):
        fm = {"range_low_mhz": 87.5, "range_high_mhz": 108.0}
        self.assertEqual(sp.seek_band(98.0, fm, (24.0, 1766.0)), (87.5, 108.0))
        self.assertEqual(sp.seek_band(300.0, fm, (24.0, 1766.0)), (290.0, 310.0))
        self.assertEqual(sp.seek_band(26.0, None, (24.0, 1766.0)), (24.0, 36.0))

    def test_the_retune_rule_is_the_engines(self):
        e = make_sdr()
        self.assertFalse(sp.needs_retune(e.center_mhz + 1.0, e.center_mhz, e.sample_rate))
        self.assertTrue(sp.needs_retune(e.center_mhz + 1.1, e.center_mhz, e.sample_rate))
        self.assertTrue(sp.needs_retune(98.0, None, e.sample_rate))


@unittest.skipIf(sp is None, "numpy not installed")
class SeekerTests(unittest.TestCase):
    def seeker(self, radio, direction=1, low=87.5, high=108.0, scanning=False):
        s = sp.Seeker()
        s.begin(direction, radio.tuned, low, high, RATE, 0.0, scanning=scanning)
        return s

    def test_a_seek_stops_on_the_next_signal_in_the_window(self):
        radio = Radio(98.0, [(98.0, 180, 25), (98.6, 180, 25)])
        s = self.seeker(radio)
        hits, messages = radio.run(s)
        self.assertEqual(hits, [98.6])
        self.assertFalse(s.active)
        self.assertIn("Signal at 98.600 MHz", messages[0])
        self.assertIn("dB over noise", messages[0])
        self.assertEqual(radio.retunes, 0)

    def test_a_seek_moves_the_dongle_window_by_window_until_it_finds_one(self):
        radio = Radio(98.0, [(104.3, 180, 25)])
        s = self.seeker(radio)
        hits, _ = radio.run(s)
        self.assertEqual(hits, [104.3])
        self.assertGreaterEqual(radio.retunes, 2)

    def test_a_seek_down_finds_the_signal_below(self):
        radio = Radio(98.0, [(93.2, 180, 25), (101.0, 180, 25)])
        s = self.seeker(radio, direction=-1)
        hits, _ = radio.run(s)
        self.assertEqual(hits, [93.2])

    def test_a_seek_wraps_round_the_end_of_the_band(self):
        radio = Radio(106.0, [(89.1, 180, 25)])
        s = self.seeker(radio)
        hits, _ = radio.run(s)
        self.assertEqual(hits, [89.1])

    def test_a_seek_that_finds_nothing_says_so_and_stops(self):
        radio = Radio(98.0, [])
        s = self.seeker(radio)
        hits, messages = radio.run(s)
        self.assertEqual(hits, [])
        self.assertEqual(messages, ["Seek found nothing in this band."])
        self.assertFalse(s.active)

    def test_signals_outside_the_band_do_not_stop_a_seek(self):
        radio = Radio(107.0, [(108.6, 180, 25), (88.0, 180, 25)])
        s = self.seeker(radio)
        hits, _ = radio.run(s)
        self.assertEqual(hits, [88.0])

    def test_a_narrow_band_is_searched_without_moving_the_dongle(self):
        radio = Radio(27.9, [(27.65, 10, 25)])
        s = self.seeker(radio, low=27.60125, high=27.99125)
        hits, _ = radio.run(s)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0], 27.65, delta=0.003)
        self.assertEqual(radio.retunes, 0)

    def test_a_band_a_little_wider_than_the_window_is_still_searched_to_its_far_end(self):
        radio = Radio(101.5, [(100.1, 180, 25)])
        s = self.seeker(radio, low=100.0, high=102.5)
        hits, _ = radio.run(s)
        self.assertEqual(hits, [100.1])

    def test_the_old_picture_is_not_searched_while_the_dongle_is_moving(self):
        s = sp.Seeker()
        s.begin(1, 98.0, 87.5, 108.0, RATE, 0.0)
        quiet = spectrum(98.0)
        mhz, message = s.step(0.1, quiet, 98.0, False)
        self.assertAlmostEqual(mhz, 99.92)
        self.assertIsNone(message)
        stale = spectrum(98.0, [(98.5, 180, 25)])
        for t in (0.2, 0.5, 1.0, 2.0):                 # still showing the old place, however long it takes
            self.assertIsNone(s.step(t, stale, 98.0, False))
        self.assertIsNone(s.step(2.1, spectrum(99.92), 99.92, True))       # engine still busy
        self.assertTrue(s.active)

    def test_a_seek_gives_up_when_the_radio_stops_answering(self):
        s = sp.Seeker()
        s.begin(1, 98.0, 87.5, 108.0, RATE, 0.0)
        self.assertIsNone(s.step(1.0, [], None, False))
        mhz, message = s.step(sp.Seeker.STALL + 1.0, [], None, False)
        self.assertIsNone(mhz)
        self.assertIn("no fresh spectrum", message)
        self.assertFalse(s.active)

    def test_a_scan_waits_on_each_signal_then_carries_on_to_the_next(self):
        radio = Radio(98.0, [(98.6, 180, 25), (99.3, 180, 25), (103.0, 180, 25)])
        s = self.seeker(radio, scanning=True)
        hits, messages = radio.run(s, until_tunes=1)
        self.assertEqual(hits, [98.6])
        self.assertTrue(s.active)
        self.assertIn("holds here 5 s", messages[0])
        self.assertEqual(s.describe(), "Scan up: holding")
        first = radio.now
        hits, _ = radio.run(s, start=first, until_tunes=1)
        self.assertEqual(hits, [99.3])
        self.assertGreaterEqual(radio.now - first, sp.Seeker.DWELL - 0.2)
        hits, _ = radio.run(s, start=radio.now, until_tunes=1)
        self.assertEqual(hits, [103.0])

    def test_a_scan_goes_round_the_band_and_comes_back_to_the_first_signal(self):
        radio = Radio(98.0, [(98.6, 180, 25), (103.0, 180, 25)])
        s = self.seeker(radio, scanning=True)
        hits, _ = radio.run(s, seconds=200, until_tunes=3)
        self.assertEqual(hits, [98.6, 103.0, 98.6])

    def test_a_scan_of_an_empty_band_stops_by_itself(self):
        radio = Radio(98.0, [])
        s = self.seeker(radio, scanning=True)
        _, messages = radio.run(s)
        self.assertEqual(messages, ["Scan found nothing in this band."])
        self.assertFalse(s.active)

    def test_stopping_ends_it_at_once(self):
        radio = Radio(98.0, [(98.6, 180, 25)])
        s = self.seeker(radio, scanning=True)
        radio.run(s, until_tunes=1)
        s.stop()
        self.assertFalse(s.active)
        self.assertIsNone(s.describe())
        self.assertIsNone(s.step(99.0, spectrum(98.0), 98.0, False))

    def test_the_words_for_the_status_line(self):
        s = sp.Seeker()
        self.assertIsNone(s.describe())
        s.begin(-1, 98.0, 87.5, 108.0, RATE, 0.0)
        self.assertEqual(s.describe(), "Seeking down")
        s.begin(1, 98.0, 87.5, 108.0, RATE, 0.0, scanning=True)
        self.assertEqual(s.describe(), "Scanning up")

    def test_a_scan_remembers_the_way_the_last_seek_went(self):
        s = sp.Seeker()
        self.assertEqual(s.direction, 1)
        s.begin(-1, 98.0, 87.5, 108.0, RATE, 0.0)
        s.stop()
        self.assertEqual(s.direction, -1)


@unittest.skipIf(sp is None, "numpy not installed")
class EngineGlueTests(unittest.TestCase):
    def engine(self):
        e = make_sdr()
        e.active = True
        e.band_profile_name = "FM BC"
        e.set_modulation("WFM")
        e.freq_mhz = e.center_mhz = 98.0
        e.pending_freq = None
        e.changing_freq = False
        return e

    def test_a_seek_started_from_the_engine_uses_its_band_and_mode(self):
        e = self.engine()
        s = sp.Seeker()
        sp.start_seek(s, e, 1, 0.0)
        self.assertTrue(s.active)
        self.assertEqual((s.low, s.high), (87.5, 108.0))
        self.assertEqual(s.modulation, "WFM")
        self.assertFalse(s.scanning)

    def test_a_cb_seek_snaps_to_the_channel_plan(self):
        e = self.engine()
        e.band_profile_name = "UK CB FM"
        e.set_modulation("NFM")
        e.freq_mhz = e.center_mhz = 27.78125
        s = sp.Seeker()
        sp.start_seek(s, e, 1, 0.0)
        self.assertEqual(len(s.channels), 40)
        e.spectrum = spectrum(27.78125, [(27.8415, 10, 25)])
        e.spectrum_center_mhz = 27.78125
        sp.run_seek(s, e, 1.0)
        self.assertEqual(e.pending_freq, 27.84125)

    def test_a_found_signal_is_tuned_through_the_engines_own_tune_request(self):
        e = self.engine()
        e.spectrum = spectrum(98.0, [(98.6, 180, 25)])
        e.spectrum_center_mhz = 98.0
        s = sp.Seeker()
        sp.start_seek(s, e, 1, 0.0)
        e.dirty = False
        message = sp.run_seek(s, e, 1.0)
        self.assertIn("Signal at 98.600 MHz", message)
        self.assertEqual(e.pending_freq, 98.6)
        self.assertTrue(e.dirty)
        self.assertFalse(s.active)

    def test_nothing_is_tuned_while_the_engine_is_still_changing_frequency(self):
        e = self.engine()
        e.spectrum = spectrum(98.0, [(98.6, 180, 25)])
        e.spectrum_center_mhz = 98.0
        s = sp.Seeker()
        sp.start_seek(s, e, 1, 0.0)
        e.changing_freq = True
        self.assertIsNone(sp.run_seek(s, e, 1.0))
        self.assertIsNone(e.pending_freq)

    def test_an_idle_seeker_never_touches_the_engine(self):
        e = self.engine()
        e.spectrum = spectrum(98.0, [(98.6, 180, 25)])
        e.spectrum_center_mhz = 98.0
        self.assertIsNone(sp.run_seek(sp.Seeker(), e, 1.0))
        self.assertIsNone(e.pending_freq)

    def test_the_panel_only_tunes_and_listens_it_has_no_way_to_transmit(self):
        source = inspect.getsource(sp)
        for word in ("rtl_tx", "hackrf", "transmit(", "subprocess"):
            self.assertNotIn(word, source)


class FakeEngine:
    def __init__(self, bias):
        self.bias_t_enabled = bias
        self.calls = []

    def stop_audio(self):
        self.calls.append("stop_audio")

    def stop_scan(self):
        self.calls.append("stop_scan")

    def set_bias_t(self, on):
        self.calls.append(("bias", on))
        self.bias_t_enabled = on
        return True

    def shutdown(self):
        self.calls.append("shutdown")


@unittest.skipIf(sp is None, "numpy not installed")
class BiasSafetyTests(unittest.TestCase):
    def test_leaving_the_tab_switches_bias_off_before_the_dongle_is_given_back(self):
        e = FakeEngine(True)
        sp.close_engine(e)
        self.assertEqual(e.calls, ["stop_audio", "stop_scan", ("bias", False), "shutdown"])

    def test_bias_is_left_alone_when_it_was_never_switched_on(self):
        e = FakeEngine(False)
        sp.close_engine(e)
        self.assertEqual(e.calls, ["stop_audio", "stop_scan", "shutdown"])

    def test_the_dongle_is_still_given_back_when_something_fails_on_the_way(self):
        e = FakeEngine(True)
        e.stop_audio = lambda: (_ for _ in ()).throw(OSError("gone"))
        sp.close_engine(e)
        self.assertIn("shutdown", e.calls)

    def test_bias_is_only_ever_switched_on_by_the_switch(self):
        source = inspect.getsource(sp)
        self.assertEqual(source.count("set_bias_t("), 2)            # close_engine (off) and the switch's handler
        self.assertNotIn("set_bias_t(True)", source)
        handler = source.split("def toggle_bias")[1].split("def bias_done")[0]
        self.assertIn("set_bias_t(wanted)", handler)
        for part in ("def activate", "def started", "def tick", "def refresh_labels"):
            body = source.split(part)[1].split("\n        def ")[0]
            self.assertNotIn("bias_t(", body, part)

    def test_the_page_adds_no_timer_of_its_own(self):
        body = inspect.getsource(sp.build_panel)
        self.assertEqual(body.count("QTimer("), 1)
        self.assertEqual(sp.TICK_MS, 100)


if __name__ == "__main__":
    unittest.main()
