"""
What became of a message this app sent, said honestly. Pure, so it can be tested without a radio.

The Meshtastic library's sendText() hands the message to the radio and returns the packet, whose `id` identifies
it. That is all that is known unless an acknowledgement was asked for: "handed to the radio" does not mean anyone
received it. Asking (wantAck) makes the mesh retry a message that is not acknowledged, so the radio sends more; it
is a setting that is off unless the person switches it on (Settings, LoRa).

When it was asked for, the library publishes the answer as a routing packet (bus topic
"meshtastic.receive.routing") whose decoded.requestId is the id of the sent packet and whose
decoded.routing.errorReason is "NONE" for success; any other reason is a failure. With no answer within about a
minute the message is shown as not acknowledged. An answer that comes later still counts.
"""

ACK_TIMEOUT_SECONDS = 60
FORGET_AFTER_SECONDS = 600           # how long the engine goes on waiting for a late answer before forgetting the id

HANDED, WAITING, ACKED, FAILED, NO_ACK = "handed", "waiting", "acked", "failed", "no_ack"
FINISHED = (ACKED, FAILED)


def packet_id(result):
    """The id of the packet sendText() returned (a protobuf object or a dict), or None."""
    value = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value or None


def new_delivery(sent_id, want_ack, now):
    """The state of a message just handed to the radio. It waits for an acknowledgement only when one was asked
    for and the packet's id is known, since the answer is matched by that id."""
    waiting = bool(want_ack) and sent_id is not None
    return {"id": sent_id, "want_ack": bool(want_ack), "state": WAITING if waiting else HANDED,
            "reason": None, "sent_at": now}


def routing_reply(packet):
    """(request id, error reason) from a routing packet, or None when it is not an answer to a sent packet. A
    reason left out, or given as 0, is "NONE": the library leaves out a field at its default value."""
    if not isinstance(packet, dict):
        return None
    decoded = packet.get("decoded")
    if not isinstance(decoded, dict):
        return None
    try:
        request = int(decoded.get("requestId") or 0)
    except (TypeError, ValueError):
        return None
    routing = decoded.get("routing")
    if not request or not isinstance(routing, dict):
        return None
    reason = routing.get("errorReason", "NONE")
    if reason in (None, 0, "", "0"):
        reason = "NONE"
    return request, str(reason)


def after_routing(delivery, reason):
    """The state once the library has answered: acknowledged for "NONE", failed with the reason for anything else.
    Only a message that asked for an acknowledgement changes, and a finished one stays as it is."""
    if not isinstance(delivery, dict) or not delivery.get("want_ack") or delivery.get("state") in FINISHED:
        return delivery
    if reason == "NONE":
        return dict(delivery, state=ACKED, reason=None)
    return dict(delivery, state=FAILED, reason=reason)


def after_time(delivery, now, timeout=ACK_TIMEOUT_SECONDS):
    """The state once time has passed: a message still waiting after `timeout` seconds is not acknowledged."""
    if isinstance(delivery, dict) and delivery.get("state") == WAITING:
        try:
            late = now - float(delivery.get("sent_at") or 0) >= timeout
        except (TypeError, ValueError):
            late = True
        if late:
            return dict(delivery, state=NO_ACK)
    return delivery


def can_forget(delivery, now, keep=FORGET_AFTER_SECONDS):
    """Whether the engine can stop listening for an answer about this message."""
    if not isinstance(delivery, dict) or delivery.get("state") in FINISHED + (HANDED,):
        return True
    try:
        return now - float(delivery.get("sent_at") or 0) >= keep
    except (TypeError, ValueError):
        return True


def delivery_text(delivery):
    """The words shown beside a sent message."""
    if not isinstance(delivery, dict):
        return ""
    state = delivery.get("state")
    if state == WAITING:
        return "handed to the radio, waiting for an acknowledgement"
    if state == ACKED:
        return "acknowledged"
    if state == FAILED:
        return f"failed: {delivery.get('reason') or 'unknown reason'}"
    if state == NO_ACK:
        return "no acknowledgement"
    return "handed to the radio"


def want_ack_setting(value):
    """The "Ask for acknowledgement" setting: on only when it was switched on, never by default."""
    return value is True
