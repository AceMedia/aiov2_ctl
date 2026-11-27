"""The looks in Settings > General: high contrast, and the plain and black backgrounds, shown on the Overview."""


def clip(rec):
    win = rec.win
    rec.settings("General", 1200)
    win.set_contrast(True)
    rec.wait(700)
    rec.shot(1500)
    rec.tab("Overview", 1500)
    win.set_contrast(False)
    rec.wait(700)
    rec.shot(900)
    win.set_background("plain")
    rec.wait(400)
    rec.shot(1300)
    win.set_background("black")
    rec.wait(400)
    rec.shot(1300)
    rec.tab("Home", 1300)
    win.set_background("gradient")
    rec.wait(400)
    rec.shot(1000)
