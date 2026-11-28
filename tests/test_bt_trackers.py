"""
Tests for naming item trackers from advertisement data, and for the scan keeping that data. No radio needed.

    python3 -m unittest discover -s tests
"""
import os
import struct
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402
from ops_gui import bt_panel as bt  # noqa: E402
from ops_gui import bt_trackers as tr  # noqa: E402


def ad(*fields):
    return b"".join(bytes([len(body) + 1, kind]) + body for kind, body in fields)


class MarkerTests(unittest.TestCase):
    def test_apple_offline_finding(self):
        found = tr.identify({"mfr_data": {"004c": "1219" + "00" * 25}})
        self.assertEqual(found, {"kind": "apple_find_my", "label": "Apple Find My tracker (AirTag or similar)",
                                 "basis": "manufacturer data 004C, type 12"})

    def test_samsung_smarttag(self):
        found = tr.identify({"service_uuids": ["fd5a"]})
        self.assertEqual((found["kind"], found["label"], found["basis"]),
                         ("samsung_smarttag", "Samsung SmartTag", "service FD5A"))

    def test_tile_either_number(self):
        for uuid in ("feed", "feec"):
            self.assertEqual(tr.identify({"service_uuids": [uuid]})["label"], "Tile tracker")

    def test_chipolo(self):
        self.assertEqual(tr.identify({"service_uuids": ["fe33"]})["kind"], "chipolo")

    def test_google_find_my_device_needs_its_frame_type(self):
        for frame in ("40", "41"):
            found = tr.identify({"service_uuids": ["feaa"], "service_data": {"feaa": frame + "0011"}})
            self.assertEqual(found["kind"], "google_find_my_device")
            self.assertEqual(found["basis"], f"service data FEAA, frame type {frame}")

    def test_a_service_heard_only_in_service_data_counts(self):
        self.assertEqual(tr.identify({"service_data": {"fd5a": "01"}})["kind"], "samsung_smarttag")


class NearMissTests(unittest.TestCase):
    def test_other_apple_types_are_not_trackers(self):
        for payload in ("0215" + "00" * 21, "1005" + "00" * 5, "0c0e00", "07190100"):  # iBeacon, Nearby, Handoff, AirPods
            self.assertIsNone(tr.identify({"mfr_data": {"004c": payload}}), payload)

    def test_type_12_from_another_company_is_not_apple(self):
        self.assertIsNone(tr.identify({"mfr_data": {"0075": "1219"}}))

    def test_plain_eddystone_is_not_claimed(self):
        self.assertIsNone(tr.identify({"service_uuids": ["feaa"]}))
        for frame in ("00", "10", "20", "30"):                           # UID, URL, TLM, EID
            self.assertIsNone(tr.identify({"service_uuids": ["feaa"], "service_data": {"feaa": frame + "00"}}))

    def test_ordinary_services_are_not_trackers(self):
        self.assertIsNone(tr.identify({"service_uuids": ["180f", "180d", "fe2c"]}))


class MalformedTests(unittest.TestCase):
    def test_missing_or_wrong_shapes(self):
        for device in (None, "x", {}, {"mfr_data": None}, {"mfr_data": "1219"}, {"service_uuids": "fd5a"},
                       {"service_data": ["feaa"]}, {"mfr_data": {"004c": ""}}, {"mfr_data": {"004c": "zz"}},
                       {"mfr_data": {"zz": "12"}}, {"service_uuids": [None, 3.5, True, "", "fd5a5"]},
                       {"service_data": {"feaa": "4"}}):
            self.assertIsNone(tr.identify(device), device)

    def test_raw_bytes_are_accepted_too(self):
        self.assertEqual(tr.identify({"mfr_data": {0x004C: b"\x12\x19"}})["kind"], "apple_find_my")


class UuidFormTests(unittest.TestCase):
    def test_short_and_full_forms_in_either_case(self):
        for form in ("fd5a", "FD5A", "0xFD5A", "0xfd5a", 0xFD5A, "0000fd5a-0000-1000-8000-00805f9b34fb",
                     "0000FD5A-0000-1000-8000-00805F9B34FB"):
            self.assertEqual(tr.uuid16(form), 0xFD5A, form)
            self.assertEqual(tr.identify({"service_uuids": [form]})["kind"], "samsung_smarttag", form)

    def test_128_bit_uuids_outside_the_base_are_not_shortened(self):
        self.assertIsNone(tr.uuid16("1234fd5a-0000-1000-8000-00805f9b34fb"))
        self.assertIsNone(tr.uuid16("0000fd5a-0000-1000-8000-000000000000"))
        self.assertIsNone(tr.uuid16(0x10000))

    def test_full_form_keys_in_service_data(self):
        found = tr.identify({"service_data": {"0000FEAA-0000-1000-8000-00805F9B34FB": "40"}})
        self.assertEqual(found["kind"], "google_find_my_device")


class ScanKeepsTheFieldsTests(unittest.TestCase):
    """The scan already receives these bytes; it now keeps them instead of dropping them."""

    def test_manufacturer_and_service_data_are_kept(self):
        out = a.bt_parse_ad(ad((0xFF, struct.pack("<H", 0x004C) + b"\x12\x19\xaa"),
                               (0x16, struct.pack("<H", 0xFEAA) + b"\x40\x01")))
        self.assertEqual(out["mfr_data"], {"004c": "1219aa"})
        self.assertEqual(out["service_data"], {"feaa": "4001"})
        self.assertEqual(out["services"], [0xFEAA])

    def test_kept_bytes_are_capped(self):
        out = a.bt_parse_ad(ad((0xFF, struct.pack("<H", 0x004C) + b"\x12" * 29)))
        self.assertEqual(len(out["mfr_data"]["004c"]), 2 * a.BT_KEEP_BYTES)

    def test_a_device_carries_them_into_the_snapshot_and_is_named(self):
        scan = a.BtScan()
        report = {"addr": "46:05:04:03:02:01", "kind": "private (changes)", "rssi": -60,
                  **a.bt_parse_ad(ad((0xFF, struct.pack("<H", 0x004C) + b"\x12\x19"),
                                     (0x03, struct.pack("<H", 0xFD5A))))}
        scan.add(report, 1.0)
        (dev,) = scan.snapshot(2.0, "hci1", "legacy")["devices"]
        self.assertEqual((dev["mfr_data"], dev["service_uuids"]), ({"004c": "1219"}, ["fd5a"]))
        self.assertEqual(tr.identify(dev)["kind"], "apple_find_my")     # the Apple marker is checked first


class RowTests(unittest.TestCase):
    SNAP = {"t": 100.0, "devices": [
        {"addr": "46:05:04:03:02:01", "kind": "private (changes)", "maker": "Apple", "services": [],
         "mfr_data": {"004c": "1219"}, "rssi": -60, "best": -55, "count": 3, "last": 100.0},
        {"addr": "c6:05:04:03:02:01", "kind": "random (fixed)", "maker": "", "services": ["Tile tracker"],
         "service_uuids": ["feed"], "rssi": -70, "count": 1, "last": 100.0},
        {"addr": "11:22:33:44:55:66", "kind": "public", "maker": "Acme", "services": ["weight scale"],
         "service_uuids": ["181d"], "rssi": -50, "count": 1, "last": 100.0},
    ]}

    def test_the_row_shows_the_label(self):
        rows = bt.device_rows(self.SNAP)
        self.assertEqual(rows[0]["what"], "Apple Find My tracker (AirTag or similar)")
        self.assertEqual(rows[1]["what"], "Tile tracker")                 # not said twice
        self.assertEqual(rows[2]["what"], "weight scale")

    def test_the_tooltip_says_why_and_nothing_more(self):
        tip = bt.tracker_tip(self.SNAP["devices"][0])
        self.assertIn("manufacturer data 004C, type 12", tip)
        self.assertEqual(bt.tracker_tip(self.SNAP["devices"][2]), "")
        self.assertIn("normal", tr.NOTE)

    def test_the_wording_claims_no_more_than_it_knows(self):
        for found in (tr.identify(d) for d in self.SNAP["devices"][:2]):
            text = (found["label"] + " " + found["basis"]).lower()
            for word in ("following", "person", "metres", "direction", "owner", "stalk"):
                self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
