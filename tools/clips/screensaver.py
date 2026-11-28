"""The Mandelbrot screensaver: a minute and a half of the dive, played back in a few seconds."""


def clip(rec):
    win = rec.win
    rec.tab("Overview", 600)
    win.saver_start("mandelbrot")
    rec.wait(6000)
    rec.watch(92, every=4, play_ms=300)
    win.saver_stop()
    rec.wait(400)
