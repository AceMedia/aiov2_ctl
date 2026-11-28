"""One way to write a mesh node's identity, used everywhere two are compared.

The Meshtastic library gives a node as a number in some places (the radio's own node number) and as a name
like "!a1b2c3d4" in others (the sender of a packet, the keys of its node list). The name is the number in
hexadecimal. Comparing one form with the other never matches, which is how the app failed to recognise its
own node. Plain Python, no radio needed."""


def canonical(value):
    """The node as "!xxxxxxxx" (eight lower-case hexadecimal digits), from a number, a string of decimal
    digits or a name already in that form. None stays None; anything else comes back as its own text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return "!%08x" % (value & 0xFFFFFFFF)
    text = str(value).strip()
    if text.startswith("!"):
        return text.lower()
    if text.isdigit():
        return "!%08x" % (int(text) & 0xFFFFFFFF)
    return text


def same(a, b):
    """Whether two identities are the same node, whichever form each is in. Nothing never matches."""
    if a is None or b is None:
        return False
    return canonical(a) == canonical(b)
