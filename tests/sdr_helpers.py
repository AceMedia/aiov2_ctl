import subprocess
import tempfile
import unittest
from unittest import mock

try:
    import numpy
except ImportError:  # the SDR object builds its audio filter with numpy
    numpy = None

from ops_agent import sdr as sdrmod
from ops_agent.sdr import SDR

if numpy is not None:
    sdrmod.np = numpy

_MEMORY_DIR = tempfile.mkdtemp(prefix="sdr-test-")


def make_sdr(stderr="", side_effect=None, memory_path=None):
    """Build an SDR without touching hardware: every subprocess.run is mocked."""
    if numpy is None:
        raise unittest.SkipTest("numpy not installed")
    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "ps":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if side_effect:
            raise side_effect
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=stderr)

    with mock.patch.object(sdrmod.subprocess, "run", fake_run), \
            mock.patch.object(sdrmod, "dvb_driver_loaded", return_value=False), \
            mock.patch.object(sdrmod, "SDR_MEMORY_PATH", memory_path or (_MEMORY_DIR + "/none.json")), \
            mock.patch.object(sdrmod.atexit, "register"):
        return SDR()


class FakeProc:
    def __init__(self, rc=None):
        self.rc = rc
        self.terminated = False
        self.killed = False
        self.waited = False
        self.pid = 1234
        self.stdin = None
        self.stdout = mock.Mock()
        self.stderr = None

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True
        self.rc = -15

    def kill(self):
        self.killed = True
        self.rc = -9

    def wait(self, timeout=None):
        self.waited = True
        return self.rc
