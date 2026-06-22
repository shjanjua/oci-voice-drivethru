"""One-way OrderState push to the kiosk over the LiveKit data channel (plan §4.9).

Control/PTT go via RPC (control.py), NOT here. The kiosk renders only the latest
snapshot (versioned envelope), so a dropped/reordered packet can't show a stale order.
"""
from __future__ import annotations

import json

ORDER_TOPIC = "order"


async def publish(room, envelope: dict, topic: str = ORDER_TOPIC) -> None:
    if room is None or room.local_participant is None:
        return
    payload = json.dumps(envelope).encode("utf-8")
    try:
        await room.local_participant.publish_data(payload, reliable=True, topic=topic)
    except Exception:  # never let a UI push break the conversation
        pass
