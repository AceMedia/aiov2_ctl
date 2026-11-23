"""
Tests for the Flipper live screen: decoding, drawing and the stream loop.

    python3 -m unittest discover -s tests
"""
import os
import struct
import sys
import unittest
import zlib
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


def blank():
    return bytearray(a.FLIPPER_SCREEN_BYTES)


class DecodeTests(unittest.TestCase):
    def test_top_left_pixel(self):
        d = blank()
        d[0] = 0x01
        rows = a.decode_flipper_frame(bytes(d))
        self.assertTrue(rows[0][0])
        self.assertEqual(sum(map(sum, rows)), 1)

    def test_bit_and_page_mapping(self):
        d = blank()
        d[128 + 5] = 0x80          # page 1, x = 5, bit 7 -> y = 15
        rows = a.decode_flipper_frame(bytes(d))
        self.assertTrue(rows[15][5])
        self.assertEqual(sum(map(sum, rows)), 1)

    def test_flipped_orientation_turns_it_round(self):
        d = blank()
        d[0] = 0x01
        rows = a.decode_flipper_frame(bytes(d), orientation=1)
        self.assertTrue(rows[63][127])

    def test_wrong_size_is_refused(self):
        with self.assertRaises(a.FlipperError):
            a.decode_flipper_frame(b"\x00" * 10)


class DrawTests(unittest.TestCase):
    def test_braille_size_and_blank(self):
        rows = a.decode_flipper_frame(bytes(blank()))
        lines = a.frame_to_braille(rows).split("\n")
        self.assertEqual((len(lines), len(lines[0])), (16, 64))
        self.assertEqual(set("".join(lines)), {"⠀"})

    def test_braille_single_dot(self):
        d = blank()
        d[0] = 0x01
        text = a.frame_to_braille(a.decode_flipper_frame(bytes(d)))
        self.assertEqual(text[0], chr(0x2801))

    def test_png_is_valid(self):
        d = blank()
        d[0] = 0xFF
        png = a.frame_to_png(a.decode_flipper_frame(bytes(d)), scale=2)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        pos, chunks = 8, {}
        while pos < len(png):
            n = struct.unpack(">I", png[pos:pos + 4])[0]
            kind, body = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + n]
            crc = struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0]
            self.assertEqual(crc, zlib.crc32(kind + body) & 0xFFFFFFFF)
            chunks[kind] = body
            pos += 12 + n
        w, h = struct.unpack(">II", chunks[b"IHDR"][:8])
        self.assertEqual((w, h), (256, 128))
        raw = zlib.decompress(chunks[b"IDAT"])
        self.assertEqual(len(raw), h * (1 + w // 8))


class StreamTests(unittest.TestCase):
    def make_rpc(self, messages):
        rpc = a.FlipperRpc(mock.Mock(fd=99, timeout=1))
        rpc._frame = mock.Mock(side_effect=messages)
        return rpc

    def frame_msg(self, data):
        return {22: [a.pb_bytes_field(1, data)]}

    def test_yields_frames_and_always_stops(self):
        good = bytes(blank())
        rpc = self.make_rpc([{1: [2]}, self.frame_msg(good), self.frame_msg(b"short"),
                             self.frame_msg(good), a.FlipperError("lost")])
        sent = []
        with mock.patch.object(a.os, "write", side_effect=lambda fd, b: sent.append(b)), \
                mock.patch.object(a.time, "sleep", lambda s: None):
            got = []
            with self.assertRaises(a.FlipperError):
                for item in rpc.screen_frames(5):
                    got.append(item)
        self.assertEqual(len(got), 2)               # the short frame was ignored
        self.assertIn(a.FLIPPER_START_SCREEN, [self._field(s) for s in sent])
        self.assertEqual(self._field(sent[-1]), a.FLIPPER_STOP_SCREEN)

    def test_closing_the_generator_stops_the_stream(self):
        rpc = self.make_rpc([self.frame_msg(bytes(blank()))] * 3)
        sent = []
        with mock.patch.object(a.os, "write", side_effect=lambda fd, b: sent.append(b)), \
                mock.patch.object(a.time, "sleep", lambda s: None):
            gen = rpc.screen_frames(5)
            next(gen)
            gen.close()
        self.assertEqual(self._field(sent[-1]), a.FLIPPER_STOP_SCREEN)

    @staticmethod
    def _field(message):
        """The content field number of a length prefixed Main message."""
        pos = 0
        while message[pos] & 0x80:
            pos += 1
        fields = a.pb_parse(message[pos + 1:])
        return max(fields)

    def test_never_sends_button_presses(self):
        import inspect
        src = inspect.getsource(a.FlipperRpc.screen_frames) + inspect.getsource(a.flipper_screen)
        for word in ("input_event", "SendInput", "= 23"):
            self.assertNotIn(word, src)


class CommandTests(unittest.TestCase):
    def test_bad_seconds(self):
        self.assertEqual(a.flipper_command(["screen", "--seconds", "0"]), 1)

    def test_png_needs_a_name(self):
        self.assertEqual(a.flipper_command(["screen", "--png"]), 1)


if __name__ == "__main__":
    unittest.main()
