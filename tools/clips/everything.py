"""The Everything tab recording a session: counts and readings charted over a minute and a half, played back in
a few seconds. Switch the USB rail on yourself first, so the internal Wi-Fi and Bluetooth card is there to be
counted; leave GPS off, since with a fix the readout shows where the uConsole is.

This one listens for real. The session it records is saved like any other (remove it afterwards from the tab's
own Delete, or Settings > Debug > Clear), and what the scans hear goes into the app's own memory and logs as
usual. The frames show counts, not names."""


def clip(rec):
    page = rec.win.pages["Everything"]
    rec.tab("Everything", 1500)
    page.start()
    rec.wait(2500)
    rec.shot(900)
    rec.watch(90, every=3, play_ms=150)                        # 30 frames: a minute and a half in 4.5 seconds
    page.stop()
    rec.wait(2000)
    rec.shot(2500)
