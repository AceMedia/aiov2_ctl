import json
import os
import random
import tempfile
import unittest

from ops_gui import runtime as rt


def power(pct, watts=3.0, direction="discharging"):
    status = {"discharging": "Discharging", "charging": "Charging", "idle": "Not charging"}[direction]
    return {"source": "BAT" if direction == "discharging" else "AC", "status": status, "capacity": pct,
            "direction": direction, "mode": "", "voltage": 3.9, "current": round(watts / 3.9, 2), "power": watts}


def drain(est, start_pct, watts, seconds, wh_per_pct=0.25, t0=1000.0, every=5, noise=None, used=0.0):
    """Feed a battery of `wh_per_pct` being emptied at `watts`. Returns (time, percent, watt hours used)."""
    t, pct = t0, start_pct
    while t <= t0 + seconds:
        w = watts if noise is None else noise(t, watts)
        used += w * every / 3600.0
        pct = start_pct - int(used / wh_per_pct)
        est.feed(power(pct, round(w, 2)), t)
        t += every
    return t - every, pct, used


class ReadingTests(unittest.TestCase):
    def test_the_direction_comes_from_the_status_and_falls_back_to_its_words(self):
        self.assertEqual(rt.direction(power(50)), "discharging")
        self.assertEqual(rt.direction(power(50, direction="charging")), "charging")
        self.assertEqual(rt.direction({"capacity": 50, "status": "Discharging"}), "discharging")
        self.assertEqual(rt.direction({"capacity": 50, "status": "Charging"}), "charging")
        self.assertEqual(rt.direction({"capacity": 50, "status": "Full"}), "idle")

    def test_no_reading_has_no_direction(self):
        for bad in (None, {}, "x", {"status": "Discharging"}, {"capacity": None}):
            self.assertIsNone(rt.direction(bad))


class ArithmeticTests(unittest.TestCase):
    def test_the_mean_needs_a_minute_and_five_readings_behind_it(self):
        self.assertEqual(rt.mean_power([], 100), (None, 0))
        self.assertEqual(rt.mean_power([(t, 3.0) for t in range(0, 20, 5)], 20), (None, 0))
        self.assertEqual(rt.mean_power([(t, 3.0) for t in range(0, 50, 5)], 50), (None, 0))
        mean, span = rt.mean_power([(t, 3.0) for t in range(0, 65, 5)], 60)
        self.assertEqual((mean, span), (3.0, 60))

    def test_the_mean_only_looks_at_the_last_five_minutes(self):
        samples = [(t, 9.0) for t in range(0, 300, 5)] + [(t, 3.0) for t in range(300, 605, 5)]
        mean, span = rt.mean_power(samples, 600)
        self.assertEqual(mean, 3.0)
        self.assertEqual(span, 300)

    def test_one_spike_does_not_swing_the_mean(self):
        samples = [(t, 3.0) for t in range(0, 300, 5)]
        samples[30] = (150, 15.0)
        mean, _ = rt.mean_power(samples, 300)
        self.assertLess(abs(mean - 3.0), 0.25)

    def test_hours_left_is_what_remains_over_the_draw(self):
        self.assertAlmostEqual(rt.hours_left(50, 0.25, 2.5), 5.0)
        self.assertEqual(rt.hours_left(0, 0.25, 2.5), 0)

    def test_hours_left_refuses_what_it_cannot_believe(self):
        self.assertIsNone(rt.hours_left(None, 0.25, 2.5))
        self.assertIsNone(rt.hours_left(50, None, 2.5))
        self.assertIsNone(rt.hours_left(50, 0.25, None))
        self.assertIsNone(rt.hours_left(50, 0.25, 0.05))       # a draw that small is not this machine running
        self.assertIsNone(rt.hours_left(50, 50.0, 2.5))        # a 5 kWh battery is a bad sum
        self.assertIsNone(rt.hours_left(50, True, 2.5))

    def test_the_charge_rate_needs_two_percent_gained(self):
        self.assertEqual(rt.charge_rate([]), (None, 0))
        self.assertEqual(rt.charge_rate([(0, 50)]), (None, 0))
        self.assertEqual(rt.charge_rate([(0, 50), (600, 51)]), (None, 0))
        rate, gained = rt.charge_rate([(0, 50), (600, 51), (1200, 52)])
        self.assertAlmostEqual(rate, 6.0)
        self.assertEqual(gained, 2)

    def test_the_charge_rate_looks_at_the_last_few_percent_only(self):
        fast = [(i * 300, 40 + i) for i in range(6)]                       # 12% an hour early on
        slow = [(fast[-1][0] + (i + 1) * 900, 46 + i) for i in range(4)]   # 4% an hour lately
        rate, gained = rt.charge_rate(fast + slow)
        self.assertAlmostEqual(rate, 4.0)
        self.assertEqual(gained, 3)

    def test_time_to_full_is_not_guessed_near_the_top(self):
        self.assertAlmostEqual(rt.hours_to_full(60, 20.0), 2.0)
        self.assertIsNone(rt.hours_to_full(95, 20.0))
        self.assertIsNone(rt.hours_to_full(100, 20.0))
        self.assertIsNone(rt.hours_to_full(60, 0))
        self.assertIsNone(rt.hours_to_full(60, None))


class WordTests(unittest.TestCase):
    def test_durations_are_rounded_as_coarsely_as_they_deserve(self):
        self.assertEqual(rt.duration_words(2.68), "2 h 40 min")
        self.assertEqual(rt.duration_words(0.74), "45 min")
        self.assertEqual(rt.duration_words(3.02), "3 h")
        self.assertEqual(rt.duration_words(11.4), "11 h")
        self.assertEqual(rt.duration_words(0.02), "under 5 min")
        self.assertEqual(rt.duration_words(0.99), "1 h")
        self.assertEqual(rt.duration_words(30), "over 24 h")

    def test_the_brief_form_is_for_the_pixel_font(self):
        self.assertEqual(rt.duration_brief(2.68), "2H 40M")
        self.assertEqual(rt.duration_brief(2.1), "2H 10M")
        self.assertEqual(rt.duration_brief(0.74), "45M")
        self.assertEqual(rt.duration_brief(11.4), "11H")
        self.assertEqual(rt.duration_brief(0.02), "<5M")
        self.assertEqual(rt.duration_brief(30), ">24H")

    def test_the_basis_is_said_in_seconds_or_minutes(self):
        self.assertEqual(rt.span_words(90), "90 s")
        self.assertEqual(rt.span_words(295), "5 min")

    def test_home_gets_a_long_and_a_short_way_to_say_it(self):
        est = {"state": "discharging", "hours": 2.68}
        self.assertEqual(rt.short_options(est), ("~2H 40M LEFT", "~2H 40M"))
        self.assertEqual(rt.short_options({"state": "discharging", "hours": None}), ("WORKING IT OUT",))
        self.assertEqual(rt.short_options({"state": "discharging", "hours": 0.01}), ("<5M LEFT", "<5M"))
        self.assertEqual(rt.short_options({"state": "charging", "hours": 1.2}), ("~1H 10M TO FULL", "~1H 10M"))

    def test_home_says_nothing_when_there_is_nothing_honest_to_say(self):
        for est in (None, {}, {"state": "idle", "hours": None}, {"state": "charging", "hours": None},
                    {"state": None, "hours": None}):
            self.assertEqual(rt.short_options(est), ())

    def test_everything_home_may_show_fits_beside_the_watts(self):
        for hours in (0.01, 0.4, 0.74, 2.68, 9.9, 11.4, 23.9, 30):
            for state in ("discharging", "charging"):
                options = rt.short_options({"state": state, "hours": hours})
                self.assertLessEqual(len("+12.34W " + options[-1]), 19, options)     # 19: room beside "100%"

    def test_the_sentence_gives_the_time_and_what_it_rests_on(self):
        est = {"state": "discharging", "hours": 2.68, "watts": 3.12, "span": 298, "points": 4, "earlier": False,
               "wh_per_pct": 0.25, "pct": 33}
        s = rt.sentence(est)
        self.assertIn("About 2 h 40 min left at this draw", s)
        self.assertIn("3.1 W, from the last 5 min", s)
        self.assertIn("from 4% of use", s)
        self.assertIn("estimate", s)
        self.assertNotIn("earlier", s)
        self.assertIn("on an earlier run", rt.sentence(dict(est, earlier=True)))

    def test_the_sentence_says_working_it_out_and_never_a_number_without_data(self):
        s = rt.sentence({"state": "discharging", "hours": None, "wh_per_pct": None})
        self.assertIn("working it out", s)
        self.assertIn("fall 2%", s)
        self.assertNotIn("About", s)
        s = rt.sentence({"state": "discharging", "hours": None, "wh_per_pct": 0.25})
        self.assertIn("working it out", s)
        self.assertIn("a minute of readings", s)

    def test_charging_gets_a_sentence_only_with_a_rate_behind_it(self):
        self.assertEqual(rt.sentence({"state": "charging", "hours": None}), "")
        s = rt.sentence({"state": "charging", "hours": 1.2, "points": 3})
        self.assertIn("About 1 h 10 min to full", s)
        self.assertIn("last 3% gained", s)
        self.assertIn("slows near the top", s)

    def test_idle_and_no_reading_say_nothing(self):
        for est in (None, {}, {"state": "idle", "hours": None}, {"state": None}):
            self.assertEqual(rt.sentence(est), "")


class RailTests(unittest.TestCase):
    CACHE = {"GPS": {"delta": 0.4, "source": "battery"}, "USB": {"delta": 0.9, "source": "battery"},
             "SDR": {"delta": 1.5, "source": "AC"}, "LORA": {"delta": 0.3, "source": "battery"}}
    SNAP = {"rails": {"GPS": "on", "USB": "on", "SDR": "on", "LORA": "off"}}
    EST = {"state": "discharging", "hours": 2.0, "watts": 3.1, "span": 300, "points": 4, "wh_per_pct": 0.248,
           "pct": 25}

    def test_only_rails_that_are_on_and_were_measured_on_battery_count(self):
        self.assertEqual(rt.rail_draws(self.SNAP, self.CACHE), {"GPS": 0.4, "USB": 0.9})
        self.assertEqual(rt.rail_draws(self.SNAP, {}), {})
        self.assertEqual(rt.rail_draws(None, None), {})
        self.assertEqual(rt.rail_draws(self.SNAP, {"GPS": {"delta": -0.1}, "USB": "x"}), {})

    def test_the_rails_share_of_the_draw_and_the_time_without_them(self):
        lines = rt.rails_lines({"GPS": 0.4, "USB": 0.9}, self.EST)
        self.assertEqual(lines[0], "GPS and USB rails are about 1.3 W of the 3.1 W (measured earlier, one at a time).")
        # 25% of 0.248 Wh a percent is 6.2 Wh; at 1.8 W that is 3.44 h
        self.assertEqual(lines[1], "Without them: about 3 h 30 min (an estimate).")

    def test_one_rail_and_three_rails_read_properly(self):
        one = rt.rails_lines({"LORA": 0.3}, self.EST)
        self.assertTrue(one[0].startswith("The LoRa rail is about 0.3 W of the 3.1 W"))
        self.assertTrue(one[1].startswith("Without it: about"))
        three = rt.rails_lines({"GPS": 0.4, "LORA": 0.3, "USB": 0.9}, self.EST)
        self.assertTrue(three[0].startswith("GPS, LoRa and USB rails are about 1.6 W"))

    def test_no_rail_line_without_a_draw_to_compare_against(self):
        self.assertEqual(rt.rails_lines({}, self.EST), [])
        self.assertEqual(rt.rails_lines({"GPS": 0.4}, dict(self.EST, watts=None, hours=None)), [])
        self.assertEqual(rt.rails_lines({"GPS": 0.4}, {"state": "charging", "hours": 1.0}), [])
        self.assertEqual(rt.rails_lines({"GPS": 0.4}, None), [])

    def test_rails_that_add_up_to_more_than_the_draw_are_not_believed(self):
        self.assertEqual(rt.rails_lines({"GPS": 2.0, "USB": 1.5}, self.EST), [])

    def test_the_share_is_shown_but_no_time_while_the_battery_size_is_unknown(self):
        lines = rt.rails_lines({"GPS": 0.4}, dict(self.EST, hours=None, wh_per_pct=None))
        self.assertEqual(len(lines), 1)

    def test_the_card_gets_the_sentence_then_the_rails(self):
        lines = rt.card_lines(self.EST, {"GPS": 0.4})
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("About 2 h left"))
        self.assertEqual(rt.card_lines({"state": "idle", "hours": None}, {"GPS": 0.4}), [])
        self.assertEqual(rt.card_lines(None), [])


class EstimatorTests(unittest.TestCase):
    def test_a_steady_drain_is_learned_and_gives_the_right_time(self):
        est = rt.Estimator()
        t, pct, _ = drain(est, 80, 3.0, 3600)                  # 3 Wh used of a 25 Wh battery: 12 percent
        e = est.estimate(t)
        self.assertEqual(e["state"], "discharging")
        self.assertAlmostEqual(est.wh_per_pct, 0.25, delta=0.01)
        self.assertAlmostEqual(e["watts"], 3.0, places=2)
        self.assertAlmostEqual(e["hours"], pct * 0.25 / 3.0, delta=0.25)
        self.assertGreaterEqual(e["points"], 10)
        self.assertFalse(e["earlier"])

    def test_a_noisy_drain_gives_much_the_same_answer(self):
        rng = random.Random(7)
        est = rt.Estimator()
        t, pct, _ = drain(est, 80, 3.0, 3600, noise=lambda t, w: max(0.5, w + rng.uniform(-1.2, 1.2)))
        e = est.estimate(t)
        self.assertAlmostEqual(est.wh_per_pct, 0.25, delta=0.02)
        self.assertAlmostEqual(e["hours"], pct * 0.25 / 3.0, delta=0.6)

    def test_one_spike_does_not_swing_the_estimate(self):
        calm, spiky = rt.Estimator(), rt.Estimator()
        t, _, _ = drain(calm, 80, 3.0, 3600)
        drain(spiky, 80, 3.0, 3600, noise=lambda t, w: 14.0 if t == 4550.0 else w)
        a, b = calm.estimate(t)["hours"], spiky.estimate(t)["hours"]
        self.assertLess(abs(a - b) / a, 0.12)

    def test_the_slowest_refresh_the_settings_offer_still_works(self):
        est = rt.Estimator()
        t, pct, _ = drain(est, 80, 3.0, 3600, every=60)
        e = est.estimate(t)
        self.assertAlmostEqual(est.wh_per_pct, 0.25, delta=0.02)
        self.assertAlmostEqual(e["hours"], pct * 0.25 / 3.0, delta=0.4)
        self.assertEqual(rt.span_words(e["span"]), "5 min")

    def test_a_percent_that_flickers_is_not_counted_as_use(self):
        est = rt.Estimator()
        t = 1000.0
        for i in range(400):                                   # 57 56 57 56 ... for half an hour
            est.feed(power(57 - (i % 2), 3.0), t)
            t += 5
        self.assertIsNone(est.wh_per_pct)
        self.assertIsNone(est.estimate(t - 5)["hours"])

    def test_too_little_data_is_working_it_out_not_a_guess(self):
        est = rt.Estimator()
        t, _, _ = drain(est, 80, 3.0, 420)                     # seven minutes: not even two steps of the gauge
        e = est.estimate(t)
        self.assertEqual(e["state"], "discharging")
        self.assertIsNone(e["hours"])
        self.assertEqual(rt.short_options(e), ("WORKING IT OUT",))
        self.assertIn("working it out", rt.sentence(e))

    def test_one_percent_between_steps_is_not_yet_enough(self):
        est = rt.Estimator()
        t, _, used = drain(est, 80, 3.0, 300)                  # first step down at 300 s
        t, pct, _ = drain(est, 80, 3.0, 310, t0=t + 5, used=used)      # second at 600 s: one percent between
        self.assertEqual(pct, 78)
        self.assertIsNone(est.estimate(t)["hours"])
        drain(est, 80, 3.0, 320, t0=t + 5, used=used + 3.0 * 315 / 3600)
        self.assertIsNotNone(est.wh_per_pct)

    def test_a_known_battery_size_still_waits_for_a_minute_of_draw(self):
        est = rt.Estimator(wh_per_pct=0.25, points=6)
        for i in range(6):
            est.feed(power(60, 3.0), 1000.0 + i * 5)
        e = est.estimate(1025.0)
        self.assertIsNone(e["hours"])
        self.assertIn("a minute of readings", rt.sentence(e))
        for i in range(6, 20):
            est.feed(power(60, 3.0), 1000.0 + i * 5)
        e = est.estimate(1095.0)
        self.assertAlmostEqual(e["hours"], 5.0)
        self.assertTrue(e["earlier"])
        self.assertIn("on an earlier run", rt.sentence(e))

    def test_the_draw_follows_a_change_within_the_window(self):
        est = rt.Estimator(wh_per_pct=0.25, points=6)
        t = 1000.0
        for _ in range(80):
            est.feed(power(60, 2.0), t)
            t += 5
        for _ in range(61):
            est.feed(power(60, 4.0), t)
            t += 5
        self.assertAlmostEqual(est.estimate(t - 5)["hours"], 60 * 0.25 / 4.0, places=2)

    def test_charging_samples_never_count_as_draw(self):
        est = rt.Estimator(wh_per_pct=0.25, points=6)
        t = 1000.0
        for _ in range(60):
            est.feed(power(60, 9.0, "charging"), t)
            t += 5
        e = est.estimate(t - 5)
        self.assertEqual(e["state"], "charging")
        self.assertIsNone(e["hours"])
        self.assertNotIn("watts", e)
        for _ in range(30):                                     # unplugged: the 9 W of charging is not remembered
            est.feed(power(60, 3.0), t)
            t += 5
        self.assertAlmostEqual(est.estimate(t - 5)["watts"], 3.0)

    def test_charging_gives_a_time_to_full_once_it_has_watched_two_percent_arrive(self):
        est = rt.Estimator()
        t, pct = 1000.0, 50
        est.feed(power(pct, 8.0, "charging"), t)
        self.assertIsNone(est.estimate(t)["hours"])
        for i in range(1, 361):                                 # one percent every five minutes: 12% an hour
            t += 5
            pct = 50 + i // 60
            est.feed(power(pct, 8.0, "charging"), t)
            if pct < 53:
                self.assertIsNone(est.estimate(t)["hours"], pct)
        e = est.estimate(t)
        self.assertEqual(pct, 56)
        self.assertAlmostEqual(e["hours"], 44 / 12.0, delta=0.05)
        self.assertEqual(rt.short_options(e), ("~3H 40M TO FULL", "~3H 40M"))
        self.assertIsNone(est.wh_per_pct)                       # nothing about the battery's size is learned here

    def test_charging_near_the_top_gives_nothing(self):
        est = rt.Estimator()
        t = 1000.0
        for i in range(400):
            est.feed(power(91 + i // 100, 4.0, "charging"), t)
            t += 5
        self.assertIsNone(est.estimate(t - 5)["hours"])
        self.assertEqual(rt.sentence(est.estimate(t - 5)), "")

    def test_idle_gives_nothing(self):
        est = rt.Estimator(wh_per_pct=0.25, points=6)
        for i in range(100):
            est.feed(power(100, 0.1, "idle"), 1000.0 + i * 5)
        e = est.estimate(1495.0)
        self.assertEqual((e["state"], e["hours"]), ("idle", None))
        self.assertEqual(rt.card_lines(e, {"GPS": 0.4}), [])

    def test_a_jump_in_the_percentage_starts_the_sums_again(self):
        est = rt.Estimator()
        t, _, used = drain(est, 80, 3.0, 500)                   # one step seen, sums running
        est.feed(power(60, 3.0), t + 5)                         # the gauge re-thinks: 79 to 60 at once
        self.assertIsNone(est.first_edge)
        t2, pct, _ = drain(est, 60, 3.0, 1500, t0=t + 10)
        self.assertAlmostEqual(est.wh_per_pct, 0.25, delta=0.02)       # learned cleanly after the jump, not across it
        est.feed(power(pct + 5, 3.0), t2 + 5)                   # and a jump upward while discharging
        self.assertIsNone(est.first_edge)
        self.assertAlmostEqual(est.wh_per_pct, 0.25, delta=0.02)       # the size already learned is kept

    def test_a_jump_never_teaches_a_wrong_battery_size(self):
        est = rt.Estimator()
        t, _, _ = drain(est, 80, 3.0, 500)
        for i in range(1, 40):                                  # a step of 20 with almost no energy behind it
            est.feed(power(59, 3.0), t + i * 5)
        self.assertIsNone(est.wh_per_pct)

    def test_the_clock_going_backwards_starts_again_without_a_wrong_number(self):
        est = rt.Estimator()
        t, _, _ = drain(est, 80, 3.0, 1500)
        learned = est.wh_per_pct
        est.feed(power(75, 3.0), t - 3600)                      # the clock was set back an hour
        e = est.estimate(t - 3600)
        self.assertIsNone(e["hours"])                            # no draw history that makes sense any more
        self.assertEqual(est.window, [(t - 3600, 3.0)])
        self.assertIsNone(est.first_edge)
        self.assertEqual(est.wh_per_pct, learned)
        t2, pct, _ = drain(est, 75, 3.0, 1500, t0=t - 3595)
        self.assertGreater(est.estimate(t2)["hours"], 0)
        self.assertAlmostEqual(est.wh_per_pct, 0.25, delta=0.02)

    def test_a_long_silence_is_not_summed_across(self):
        est = rt.Estimator()
        t, _, used = drain(est, 80, 3.0, 400)                   # first step seen
        est.feed(power(78, 3.0), t + 4000)                      # asleep for an hour; 2% lower when it wakes
        self.assertIsNone(est.wh_per_pct)
        self.assertIsNone(est.estimate(t + 4000)["hours"])

    def test_an_old_reading_is_not_repeated_as_if_it_were_now(self):
        est = rt.Estimator()
        t, _, _ = drain(est, 80, 3.0, 1500)
        self.assertIsNotNone(est.estimate(t + 30)["hours"])
        stale = est.estimate(t + 600)
        self.assertEqual((stale["state"], stale["hours"]), (None, None))
        self.assertEqual(rt.short_options(stale), ())

    def test_readings_that_are_not_readings_are_ignored(self):
        est = rt.Estimator()
        for bad in (None, {}, {"capacity": None}, {"capacity": "x", "status": "Discharging"}, "nonsense"):
            est.feed(bad, 1000.0)
        self.assertIsNone(est.estimate(1000.0)["state"])
        est.feed({"capacity": 50, "status": "Discharging"}, 1005.0)        # no watts in it
        est.feed({"capacity": 50, "status": "Discharging", "power": "3"}, 1010.0)
        self.assertEqual(est.window, [])
        self.assertIsNone(est.estimate(1010.0)["hours"])

    def test_nothing_before_the_first_reading(self):
        e = rt.Estimator().estimate(1000.0)
        self.assertEqual((e["state"], e["hours"]), (None, None))
        self.assertEqual(rt.card_lines(e), [])


class SavedCapacityTests(unittest.TestCase):
    def test_the_learned_size_is_saved_privately_and_read_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "battery-capacity.json")
            self.assertEqual(rt.load_capacity(path), (None, 0))
            self.assertTrue(rt.save_capacity(0.2512345, 5, path))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(rt.load_capacity(path), (0.2512, 5))
            est = rt.Estimator.from_file(path)
            self.assertEqual((est.wh_per_pct, est.points, est.earlier), (0.2512, 5, True))

    def test_a_damaged_or_silly_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            for text in ("", "[1]", "{", json.dumps({"wh_per_pct": 99, "points": 5}),
                         json.dumps({"wh_per_pct": 0.25, "points": 1}), json.dumps({"wh_per_pct": "0.25", "points": 5})):
                with open(path, "w") as f:
                    f.write(text)
                self.assertEqual(rt.load_capacity(path), (None, 0), text)

    def test_the_estimator_writes_what_it_learns(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            est = rt.Estimator(path)
            saved = []
            est.save = lambda: saved.append((est.wh_per_pct, est.points))   # no thread in a test
            drain(est, 80, 3.0, 1500)
            self.assertTrue(saved)
            self.assertEqual(saved[-1], (est.wh_per_pct, est.points))

    def test_the_file_is_in_the_apps_cache_folder(self):
        old = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = "/tmp/xdg-test"
        try:
            self.assertEqual(rt.capacity_path(), "/tmp/xdg-test/ops_gui/battery-capacity.json")
        finally:
            if old is None:
                del os.environ["XDG_CACHE_HOME"]
            else:
                os.environ["XDG_CACHE_HOME"] = old


class KnownSizeTests(unittest.TestCase):
    """When the battery states its own size, that is used, whatever was learned before."""

    def drain(self, est, pct, watts=7.6, start=1000.0, seconds=400):
        for i in range(seconds // 10):
            est.feed({"status": "Discharging", "direction": "discharging", "capacity": pct, "power": watts}, start + i * 10)
        return est.estimate(start + seconds)

    def test_a_wrong_size_learned_earlier_is_replaced(self):
        from ops_gui import runtime
        est = runtime.Estimator(wh_per_pct=0.0323, points=2)          # the 3 Wh "battery" the walk left behind
        self.assertLess(self.drain(est, 45)["hours"], 0.25)           # which gave about ten minutes
        est.use_known_size(0.2479)
        hours = self.drain(est, 45, start=5000.0)["hours"]
        self.assertTrue(1.3 <= hours <= 1.6, hours)                   # 45% of 24.8 Wh at 7.6 W

    def test_nothing_is_learned_once_the_size_is_known(self):
        from ops_gui import runtime
        est = runtime.Estimator()
        est.use_known_size(0.2479)
        t = 1000.0
        for pct in (100, 100, 55, 54, 53, 52, 51, 50):               # a jump, then a fast fall
            for _ in range(6):
                est.feed({"status": "Discharging", "direction": "discharging", "capacity": pct, "power": 7.6}, t)
                t += 10
        self.assertAlmostEqual(est.wh_per_pct, 0.2479)
        self.assertTrue(est.known)

    def test_a_silly_stated_size_is_not_taken(self):
        from ops_gui import runtime
        est = runtime.Estimator()
        est.use_known_size(0)
        est.use_known_size(None)
        self.assertFalse(est.known)

    def test_the_sentence_says_where_the_size_came_from(self):
        from ops_gui import runtime
        est = runtime.Estimator()
        est.use_known_size(0.2479)
        text = runtime.sentence(self.drain(est, 45))
        self.assertIn("24.8 Wh", text)
        self.assertIn("as the battery reports it", text)


if __name__ == "__main__":
    unittest.main()
