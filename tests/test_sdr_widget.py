import unittest
from unittest import mock

try:
    import numpy
    from rich.console import Console
    from ops_agent import ui
except (ImportError, TypeError):  # textual, rich or numpy missing, or ops runtime not bootstrapped
    ui = None

try:
    from .sdr_helpers import make_sdr
except ImportError:
    from sdr_helpers import make_sdr


def render_text(widget):
    console = Console(width=100, record=True, force_terminal=False)
    console.print(widget.render())
    return console.export_text()


@unittest.skipIf(ui is None, "ui dependencies not installed")
class SDRWidgetTests(unittest.TestCase):
    def widget(self, **kw):
        sdr = make_sdr(**kw)
        app = mock.Mock(auto_squelch_enabled=False, sdr_bandwidth=0.2)
        return ui.SDRWidget(sdr, True, app), sdr

    def test_missing_dongle_message_is_shown(self):
        w, sdr = self.widget(stderr="No supported devices found.")
        self.assertIn("No RTL-SDR found", render_text(w))

    def test_lost_link_shows_error_and_how_to_retry(self):
        w, sdr = self.widget()
        sdr.link_state = "lost"
        sdr.last_error = "Dongle dropped out (USB read failed). Replug or check the SDR rail"
        sdr.panadapter_proc = None
        out = render_text(w)
        self.assertIn("dropped out", out)
        self.assertIn("try again", out)

    def test_level_line_with_spectrum(self):
        w, sdr = self.widget()
        sdr.spectrum = [10.0] * 80 + [30.0] * 20
        sdr.spectrum_center_mhz = sdr.freq_mhz
        sdr.s_meter_db = 25.0
        sdr.squelch_level = 30
        sdr.squelch_open = False
        line = w._level_line()
        self.assertIn("+15 dB over noise", line)
        self.assertIn("Sq 30", line)
        self.assertIn("shut", line)

    def test_level_line_without_signal(self):
        w, _ = self.widget()
        self.assertIn("--", w._level_line())

    def test_audio_error_is_shown(self):
        w, sdr = self.widget()
        sdr.audio_error = "paplay not found (install pulseaudio-utils)"
        self.assertIn("paplay not found", render_text(w))


if __name__ == "__main__":
    unittest.main()
