"""The key check says "cannot be told" for a psk that is a number, rather than turning it into zero bytes."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import lora  # noqa: E402


def settings(psk):
    return types.SimpleNamespace(psk=psk, name="")


class OddPskTests(unittest.TestCase):
    def test_a_number_cannot_be_told(self):
        # bytes(1) is one zero byte, not the default key's b"\x01"; bytes(2) two zero bytes. Neither says anything.
        for psk in (0, 1, 2, 16, True, False):
            self.assertEqual(lora.channel_key_kind(settings(psk)), "unknown", psk)

    def test_bytes_still_decide(self):
        self.assertEqual(lora.channel_key_kind(settings(b"\x01")), "default")
        self.assertEqual(lora.channel_key_kind(settings(bytearray(b"\x01"))), "default")
        self.assertEqual(lora.channel_key_kind(settings(b"\x12" * 32)), "private")


if __name__ == "__main__":
    unittest.main()

