"""The one coordinate converter every view uses."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import geo, lora  # noqa: E402
from ops_gui import lora_panel as lp  # noqa: E402


class LatLonTests(unittest.TestCase):
    def test_floats_and_the_integer_form(self):
        self.assertEqual(geo.latlon({"latitude": 51.5, "longitude": -0.1}), (51.5, -0.1))
        lat, lon = geo.latlon({"latitudeI": 515000000, "longitudeI": -1000000})
        self.assertAlmostEqual(lat, 51.5)
        self.assertAlmostEqual(lon, -0.1)
        self.assertEqual(geo.latlon({"latitude": "51.5", "longitude": "1"}), (51.5, 1.0))

    def test_edges_of_the_range(self):
        self.assertEqual(geo.latlon({"latitude": 90, "longitude": 180}), (90.0, 180.0))
        self.assertEqual(geo.latlon({"latitude": -90, "longitude": -180}), (-90.0, -180.0))
        self.assertIsNone(geo.latlon({"latitude": 90.0001, "longitude": 0.5}))
        self.assertIsNone(geo.latlon({"latitude": 10, "longitude": -180.5}))
        self.assertIsNone(geo.latlon({"latitudeI": 910000000, "longitudeI": 10}))

    def test_what_is_not_a_position(self):
        for bad in ({"latitude": 0, "longitude": 0}, {"latitudeI": 0, "longitudeI": 0},
                    {"latitude": float("nan"), "longitude": 1}, {"latitude": 1, "longitude": float("inf")},
                    {"latitude": float("-inf"), "longitude": 1}, {"latitude": True, "longitude": 1},
                    {"latitude": "north", "longitude": 1}, {"latitude": 1}, {}, None, "51,0", []):
            self.assertIsNone(geo.latlon(bad), bad)

    def test_every_view_agrees(self):
        info = {"position": {"latitude": float("nan"), "longitude": 1.0}}
        self.assertIsNone(lp.node_latlon(info))
        engine = lora.LoRa()
        engine._remember_node_position("!a", {"latitude": 0, "longitude": 0})
        engine._remember_node_position("!a", {"latitude": 95, "longitude": 1})
        self.assertEqual(engine.node_history.get("!a", []), [])
        engine._remember_node_position("!a", {"latitudeI": 515000000, "longitudeI": 10000000})
        self.assertAlmostEqual(engine.node_history["!a"][-1]["lat"], 51.5)

    def test_proximity_ignores_a_node_with_no_fix(self):
        engine = lora.LoRa()
        engine.node_info = {"!a": {"position": {"latitude": 0, "longitude": 0}},
                            "!b": {"position": {"latitudeI": 515000000, "longitudeI": -1000000}}}
        seen = []
        engine.update_proximity({"lat": 51.5, "lon": -0.1}, lambda *a: seen.append(a) or 0.0)
        self.assertEqual(len(seen), 1)
        self.assertAlmostEqual(seen[0][2], 51.5)
        engine.update_proximity({"lat": float("nan"), "lon": 0.1}, lambda *a: seen.append(a) or 0.0)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
