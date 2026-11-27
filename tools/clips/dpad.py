"""Moving round the app with the D-pad: down into a page, up to the row of tabs, along it, and round a page."""


def clip(rec):
    rec.tab("Home", 900)
    rec.dpad("down", "down", "down", ms=450)                   # the Home screen's rail rows
    rec.to_tabs(ms=350)                                        # back up, to the row of tabs
    rec.dpad("right", ms=800)                                  # along it: Overview
    rec.dpad("down", "right", "down", "left", "down", ms=450)  # into the page and round its switches
    rec.to_tabs(ms=300)
    rec.dpad("right", "right", ms=700)                         # Everything, then Wi-Fi
    rec.dpad("down", "right", "right", ms=500)                 # the Wi-Fi tab's own row of tabs
    rec.clear_highlight()
    rec.tab("Home", 300)
