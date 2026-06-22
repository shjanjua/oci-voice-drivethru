"""Shared session state container (AgentSession.userdata).

Holds the brain-independent order + the room (for data-channel pushes) + the DB repo.
Survives brain swaps (reset / language switch); the order is reset between customers.
"""
from __future__ import annotations

from dataclasses import dataclass

from .order_state import OrderState


@dataclass
class UserData:
    order: OrderState
    out_of_stock: str
    room: object | None = None        # rtc.Room — for one-way OrderState pushes
    db: object | None = None          # OrderRepo (membership); None => guest mode
    request_reset: object | None = None  # callable set by the supervisor (confirm/walk-off -> reset)
    request_language_switch: object | None = None  # callable(lang) set by the supervisor (set_language tool)
