import unittest

import numpy as np

from ops_gui import sense_dsp as d

RNG = np.random.default_rng(7)


def noise(n, sigma=0.5):
    return RNG.normal(0.0, sigma, n)


def quiet_link(seconds=60, rate=5.0, base=-60.0, sigma=0.4):
    n = int(seconds * rate)
    return np.arange(n) / rate, base + noise(n, sigma)


class ResampleTests(unittest.TestCase):
    def test_a_ramp_stays_a_ramp(self):
        t = np.sort(RNG.uniform(0, 10, 60))
        g, v = d.resample(t, 2.0 * t + 1.0, rate=20)
        self.assertTrue(np.allclose(v, 2.0 * g + 1.0))
        self.assertAlmostEqual(g[1] - g[0], 0.05)

    def test_too_little_data(self):
        for t, v in (([], []), ([1], [2]), ([1, 2], [float("nan"), float("nan")])):
            g, out = d.resample(t, v)
            self.assertEqual((len(g), len(out)), (0, 0))

    def test_span_keeps_the_latest(self):
        t = np.arange(0, 100, 0.2)
        g, v = d.resample(t, t, span=5)
        self.assertLessEqual(g[-1] - g[0], 5.01)
        self.assertGreater(g[0], 94)

    def test_gaps_hold_the_last_value(self):
        g, v = d.resample([0, 1, 10], [5, 5, 9], rate=2)
        self.assertEqual(v[0], 5)


class FilterTests(unittest.TestCase):
    def test_hampel_removes_a_spike_and_keeps_the_rest(self):
        x = np.full(40, -60.0) + noise(40, 0.2)
        x[20] = -20.0
        y = d.hampel(x)
        self.assertLess(abs(y[20] + 60), 2)
        self.assertTrue(np.allclose(np.delete(y, 20), np.delete(x, 20), atol=0.0) or abs(np.delete(y, 20) - np.delete(x, 20)).max() < 3)

    def test_hampel_on_short_input(self):
        self.assertEqual(list(d.hampel([1, 2])), [1, 2])

    def test_band_power_finds_the_right_band(self):
        t = np.arange(0, 20, 1 / 20)
        low = np.sin(2 * np.pi * 0.3 * t)
        high = np.sin(2 * np.pi * 3.0 * t)
        self.assertGreater(d.band_power(high, 20, 0.5, 9), 20 * d.band_power(low, 20, 0.5, 9))
        self.assertGreater(d.band_power(low, 20, 0.13, 0.6), 20 * d.band_power(high, 20, 0.13, 0.6))

    def test_a_steady_level_has_no_power(self):
        self.assertAlmostEqual(d.band_power(np.full(100, -60.0), 20, 0.5, 9), 0.0, places=6)
        self.assertEqual(d.band_power([1, 2], 20, 0.5, 9), 0.0)


class DbTests(unittest.TestCase):
    def test_to_db_and_floor(self):
        self.assertAlmostEqual(d.to_db(10.0, 1.0), 10.0)
        self.assertAlmostEqual(d.to_db(1.0, 1.0), 0.0)
        self.assertEqual(d.noise_floor([]), 0.02)
        self.assertEqual(d.noise_floor([None]), 0.02)
        self.assertAlmostEqual(d.noise_floor([1.0] * 10 + [100.0] * 2), 1.0)       # a few bursts do not raise the floor

    def test_classes(self):
        self.assertEqual([d.classify(x) for x in (-3, 0, 5, 12, 25)], ["still", "subtle", "subtle", "active", "vigorous"])


class GateTests(unittest.TestCase):
    def test_hysteresis_and_dwell(self):
        g = d.Gate(6.0, 3.5, 0.6, 2.0)
        self.assertFalse(g.update(7.0, 0.5))                 # not long enough yet
        self.assertTrue(g.update(7.0, 0.5))
        self.assertTrue(g.update(5.0, 5.0))                  # between the lines: stays on
        self.assertTrue(g.update(2.0, 1.0))                  # below, but not for two seconds
        self.assertFalse(g.update(2.0, 1.5))

    def test_a_blip_resets_the_count(self):
        g = d.Gate(6.0, 3.5, 0.6, 2.0)
        g.update(9.0, 0.4)
        g.update(0.0, 0.1)
        self.assertFalse(g.update(9.0, 0.4))


class LinkTests(unittest.TestCase):
    def run_link(self, signal, rate=5.0, update_every=1.0):
        link = d.LinkSense("ap", native_rate=rate)
        dbs = []
        for i, v in enumerate(signal):
            t = i / rate
            link.add(t, v)
            if i and i % int(rate * update_every) == 0:
                dbs.append(link.update())
        return link, dbs

    def test_a_quiet_link_stays_near_zero(self):
        t, v = quiet_link(120)
        link, dbs = self.run_link(v)
        self.assertLess(max(dbs[10:]), 8.0)

    def test_movement_stands_out_from_the_quiet(self):
        t, v = quiet_link(120)
        v[300:400] += noise(100, 4.0) + 3 * np.sin(2 * np.pi * 1.2 * t[300:400])
        link, dbs = self.run_link(v)
        self.assertGreater(max(dbs[62:82]), 10.0)
        self.assertLess(dbs[-1], 6.0)

    def test_the_band_follows_the_source_rate(self):
        self.assertLessEqual(d.LinkSense("a", 5.0).band[1], 2.3)
        self.assertEqual(d.LinkSense("a", 100.0).band[1], 9.0)
        self.assertGreater(d.LinkSense("a", 1.0).band[1], d.LinkSense("a", 1.0).band[0])

    def test_missing_readings_and_trimming(self):
        link = d.LinkSense("a", 5.0, keep_s=10)
        for i in range(200):
            link.add(i * 0.2, None if i % 3 == 0 else -60.0)
        self.assertLessEqual(link.times[-1] - link.times[0], 10.01)
        self.assertEqual(d.LinkSense("a").update(), 0.0)                 # nothing yet

    def test_reliability(self):
        link = d.LinkSense("a", 5.0)
        for i in range(100):
            link.add(i * 0.2, -60)
        self.assertGreater(link.reliability(), 0.95)
        sparse = d.LinkSense("b", 5.0)
        for i in range(0, 100, 5):
            sparse.add(i * 0.2, -60)
        self.assertLess(sparse.reliability(), 0.3)
        self.assertEqual(d.LinkSense("c").reliability(), 0.0)


class FusionTests(unittest.TestCase):
    def test_a_shared_disturbance_beats_independent_noise(self):
        n, k = 120, 6
        shared = 3 * np.sin(2 * np.pi * 1.0 * np.arange(n) / 20)
        moving = np.stack([-60 + shared + noise(n, 0.3) for _ in range(k)], axis=1)
        still = np.stack([-60 + noise(n, 0.3) for _ in range(k)], axis=1)
        first_m, rest_m = d.fusion_db(moving)
        first_s, rest_s = d.fusion_db(still)
        self.assertGreater(first_m / max(rest_m, 1e-9), 5 * first_s / max(rest_s, 1e-9))

    def test_bad_shapes(self):
        self.assertEqual(d.fusion_db([[1, 2]]), (0.0, 0.0))
        self.assertEqual(d.fusion_db(np.zeros(30)), (0.0, 0.0))


class HonestyTests(unittest.TestCase):
    def test_it_only_calculates(self):
        import inspect
        src = inspect.getsource(d).split('"""')[2]
        for word in ("subprocess", "socket", "open(", "requests", "inject", "transmit"):
            self.assertNotIn(word, src)

    def test_no_heart_rate_or_pose_claims(self):
        import inspect
        text = inspect.getsource(d).lower().split('"""')[1]
        self.assertIn("do not hold up", text)


if __name__ == "__main__":
    unittest.main()
