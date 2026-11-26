"""
Two columns of marked-up text, side by side, for the terminal console's panels.

The console used to pad the left column with an ordinary string format. That counts the colour tags
("[dim]", "[/dim]") as if they were letters, so a line with tags came out too narrow and a long line was
never cut: the right-hand column started in a different place on every line and ran into the left one.
Here each left line is measured as it will look, cut to the column with an ellipsis if it is too long, and
padded to exactly the column's width.

`text_class` is rich's Text, passed in so this module needs nothing at import time.
"""


def two_columns(left_lines, right_lines, left_width, text_class, right_width=None, gap=3):
    """One rich Text holding both columns. Every right-hand line starts `left_width + gap` cells in."""
    left_lines, right_lines = list(left_lines), list(right_lines)
    rows = max(len(left_lines), len(right_lines))
    left_lines += [""] * (rows - len(left_lines))
    right_lines += [""] * (rows - len(right_lines))
    out = text_class()
    for i, (left, right) in enumerate(zip(left_lines, right_lines)):
        cell = text_class.from_markup(left)
        cell.truncate(left_width, overflow="ellipsis", pad=True)
        out.append_text(cell)
        other = text_class.from_markup(right)
        if other.cell_len:
            out.append(" " * gap)
            if right_width:
                other.truncate(max(1, right_width), overflow="ellipsis")
            out.append_text(other)
        if i < rows - 1:
            out.append("\n")
    return out


def age_words(seconds):
    """How long ago, short enough for a table: 45s, 12m, 5h, then days. Hours alone made a node last heard in
    January read "7422h". None when it is not known."""
    if seconds is None:
        return None
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 48 * 3600:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
