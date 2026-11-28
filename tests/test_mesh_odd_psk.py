"""The default-key check says "cannot be told" for a psk that is a number, rather than turning it into zero bytes."""
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
            self.assertIsNone(lora.uses_default_key(settings(psk)), psk)

    def test_bytes_still_decide(self):
        self.assertTrue(lora.uses_default_key(settings(b"\x01")))
        self.assertTrue(lora.uses_default_key(settings(bytearray(b"\x01"))))
        self.assertFalse(lora.uses_default_key(settings(b"\x12" * 32)))


if __name__ == "__main__":
    unittest.main()

