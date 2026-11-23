"""
Tests for the Flipper RPC file transfer, using a fake Flipper on a pty.

    python3 -m unittest discover -s tests
"""
import hashlib
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


class FakeRpcFlipper:
    """CLI first, then protobuf frames after start_rpc_session."""

    def __init__(self, oversize=False):
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.files = {}
        self.dirs = {"/ext"}
        self.events = []
        self.oversize = oversize
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def send(self, cid, status=0, has_next=False, field=None, content=b""):
        body = a.pb_varint_field(1, cid) + a.pb_varint_field(2, status) + a.pb_varint_field(3, 1 if has_next else 0)
        body += a.pb_bytes_field(field if field else 4, content)
        os.write(self.master, a.pb_varint(len(body)) + body)

    def _serve(self):
        buf, rpc, writes = b"", False, {}
        while not self._stop:
            try:
                buf += os.read(self.master, 4096)
            except OSError:
                return
            while True:
                if not rpc:
                    if b"\r" not in buf:
                        break
                    line, _, buf = buf.partition(b"\r")
                    cmd = line.decode()
                    if cmd == "":
                        os.write(self.master, b"\r\n>: ")
                    elif cmd == "start_rpc_session":
                        self.events.append("start")
                        os.write(self.master, b"start_rpc_session\r\n")
                        rpc = True
                        if self.oversize:
                            os.write(self.master, a.pb_varint(1 << 20))
                    continue
                try:
                    length, pos = a.pb_read_varint(buf, 0)
                except a.FlipperError:
                    break
                if len(buf) < pos + length:
                    break
                msg = a.pb_parse(buf[pos:pos + length])
                buf = buf[pos + length:]
                cid = msg.get(1, [0])[0]
                more = bool(msg.get(3, [0])[0])
                if 19 in msg:
                    self.events.append("stop")
                    rpc = False
                    os.write(self.master, b"\r\n>: ")
                elif 24 in msg:
                    path = a.pb_parse(msg[24][0])[1][0].decode()
                    exists = path in self.files or path in self.dirs
                    self.send(cid, 0 if exists else 7)
                elif 13 in msg:
                    path = a.pb_parse(msg[13][0])[1][0].decode()
                    self.dirs.add(path)
                    self.send(cid)
                elif 11 in msg:
                    req = a.pb_parse(msg[11][0])
                    path = req[1][0].decode()
                    data = a.pb_parse(req[2][0]).get(4, [b""])[0]
                    writes[cid] = writes.get(cid, b"") + data
                    if not more:
                        self.files[path] = writes.pop(cid)
                        self.send(cid)
                elif 14 in msg:
                    path = a.pb_parse(msg[14][0])[1][0].decode()
                    digest = hashlib.md5(self.files[path]).hexdigest()
                    self.send(cid, field=15, content=a.pb_bytes_field(1, digest))
                elif 9 in msg:
                    path = a.pb_parse(msg[9][0])[1][0].decode()
                    if path not in self.files:
                        self.send(cid, 7)
                        continue
                    data = self.files[path]
                    chunks = [data[i:i + 512] for i in range(0, len(data), 512)] or [b""]
                    for n, chunk in enumerate(chunks):
                        file_msg = a.pb_bytes_field(1, a.pb_bytes_field(4, chunk))
                        self.send(cid, has_next=n < len(chunks) - 1, field=10, content=file_msg)

    def close(self):
        self._stop = True
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


class CodecTests(unittest.TestCase):
    def test_known_vector(self):
        # Main{command_id=2, storage_stat_request{path="/ext"}}, hand encoded
        self.assertEqual(
            a.pb_main(2, 24, a.pb_bytes_field(1, "/ext")),
            bytes.fromhex("0b0802c20106" + "0a042f657874"),
        )

    def test_varint_round_trip(self):
        for n in (0, 1, 127, 128, 300, 2 ** 32):
            enc = a.pb_varint(n)
            self.assertEqual(a.pb_read_varint(enc, 0)[0], n)

    def test_truncated_input_is_an_error_not_a_crash(self):
        with self.assertRaises(a.FlipperError):
            a.pb_parse(b"\x12\x05ab")
        with self.assertRaises(a.FlipperError):
            a.pb_read_varint(b"\x80", 0)


@unittest.skipUnless(hasattr(os, "openpty"), "needs a Unix pty")
class TransferTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRpcFlipper()
        self.addCleanup(self.fake.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_cmd(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = a.flipper_command([*args, "--port", self.fake.path])
        return rc, out.getvalue()

    def local(self, name, data=None):
        path = os.path.join(self.tmp.name, name)
        if data is not None:
            with open(path, "wb") as f:
                f.write(data)
        return path

    def test_put_spans_chunks_and_verifies(self):
        data = os.urandom(1300)  # three chunks
        src = self.local("a.bin", data)
        rc, out = self.run_cmd("put", src, "/ext/a.bin")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.fake.files["/ext/a.bin"], data)
        self.assertIn("Checksum verified", out)
        time.sleep(0.2)
        self.assertEqual(self.fake.events, ["start", "stop"])

    def test_put_empty_file(self):
        rc, _ = self.run_cmd("put", self.local("e", b""), "/ext/e")
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.files["/ext/e"], b"")

    def test_put_refuses_overwrite_without_force(self):
        self.fake.files["/ext/a.bin"] = b"old"
        rc, out = self.run_cmd("put", self.local("a", b"new"), "/ext/a.bin")
        self.assertEqual(rc, 1)
        self.assertIn("--force", out)
        self.assertEqual(self.fake.files["/ext/a.bin"], b"old")
        rc, _ = self.run_cmd("put", self.local("a"), "/ext/a.bin", "--force")
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.files["/ext/a.bin"], b"new")

    def test_session_is_ended_even_after_an_error(self):
        self.run_cmd("get", "/ext/missing", self.local("m"))
        time.sleep(0.2)
        self.assertEqual(self.fake.events, ["start", "stop"])

    def test_remote_paths_limited_to_sd_card(self):
        src = self.local("a", b"x")
        for bad in ("/int/boot", "/ext/../int/x", "/etc/passwd", "ext/a", "/ext/a;b"):
            rc, _ = self.run_cmd("put", src, bad)
            self.assertEqual(rc, 1, bad)
        self.assertEqual(self.fake.files, {})

    def test_put_missing_local_file(self):
        rc, out = self.run_cmd("put", self.local("nope"), "/ext/x")
        self.assertEqual(rc, 1)
        self.assertIn("not a file", out)

    def test_get_round_trip(self):
        data = os.urandom(1500)
        self.fake.files["/ext/b.bin"] = data
        dest = self.local("b.bin")
        rc, out = self.run_cmd("get", "/ext/b.bin", dest)
        self.assertEqual(rc, 0, out)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertEqual(oct(os.stat(dest).st_mode & 0o777), "0o600")

    def test_get_never_overwrites_local(self):
        self.fake.files["/ext/b"] = b"remote"
        dest = self.local("b", b"precious")
        rc, _ = self.run_cmd("get", "/ext/b", dest)
        self.assertEqual(rc, 1)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"precious")

    def test_get_missing_remote_reports_status(self):
        rc, out = self.run_cmd("get", "/ext/missing", self.local("m"))
        self.assertEqual(rc, 1)
        self.assertIn("does not exist", out)
        self.assertFalse(os.path.exists(self.local("m")))

    def test_mkdir(self):
        rc, _ = self.run_cmd("mkdir", "/ext/mine")
        self.assertEqual(rc, 0)
        self.assertIn("/ext/mine", self.fake.dirs)

    def test_usage_for_wrong_arguments(self):
        for args in (("put", "onlyone"), ("get",), ("mkdir",), ("mkdir", "a", "b")):
            rc, out = self.run_cmd(*args)
            self.assertEqual(rc, 1, args)
            self.assertIn("Usage", out)


@unittest.skipUnless(hasattr(os, "openpty"), "needs a Unix pty")
class HostileDeviceTests(unittest.TestCase):
    def test_oversized_frame_is_refused(self):
        fake = FakeRpcFlipper(oversize=True)
        self.addCleanup(fake.close)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a")
            with open(src, "wb") as f:
                f.write(b"x")
            out = io.StringIO()
            with redirect_stdout(out):
                rc = a.flipper_command(["put", src, "/ext/a", "--port", fake.path])
        self.assertEqual(rc, 1)
        self.assertIn("oversized", out.getvalue())


if __name__ == "__main__":
    unittest.main()
