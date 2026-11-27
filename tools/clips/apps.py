"""The Apps tab: tiles in a grid, moved through with the D-pad. It stays in the Games section on purpose: the
other sections list everything installed on the computer it is recorded on."""


def clip(rec):
    rec.show_tab("Apps")
    rec.tab("Apps", 1000)
    rec.dpad("down", ms=600)                                   # the row of sections
    rec.dpad("down", ms=600)                                   # into the grid
    rec.dpad("right", "right", "right", ms=400)
    rec.dpad("down", "left", ms=450)
    rec.dpad("up", "right", "right", ms=400)
    rec.dpad("up", ms=700)                                     # from the top row, back to the sections
    rec.dpad("down", ms=500)
    rec.clear_highlight()
    rec.shot(600)
