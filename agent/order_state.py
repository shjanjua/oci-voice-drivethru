"""Order state — brain-independent, lives in AgentSession.userdata.

Shared via AgentSession.userdata (survives brain swaps); cleared on per-customer reset.
to_envelope() emits the versioned data-channel snapshot the kiosk renders.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import menu


@dataclass
class OrderLine:
    kind: str                       # "drink" | "pastry"
    item_id: str
    name: str
    qty: int
    unit_price: float
    size: str | None = None         # S/M/L for drinks
    modifiers: list[str] = field(default_factory=list)
    discount: float = 0.0
    discount_reason: str | None = None

    @property
    def key(self) -> tuple:
        return (self.kind, self.item_id, self.size, tuple(sorted(self.modifiers)))

    @property
    def line_total(self) -> float:
        return round(self.unit_price * self.qty - self.discount, 2)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "item_id": self.item_id,
            "name": self.name,
            "qty": self.qty,
            "size": self.size,
            "size_name": menu.SIZES[self.size][0] if self.size else None,
            "modifiers": [_mod_label(m) for m in self.modifiers],
            "unit_price": self.unit_price,
            "discount": self.discount,
            "discount_reason": self.discount_reason,
            "line_total": self.line_total,
        }


@dataclass
class Member:
    name: str
    membership_number: str | None = None
    preferred_language: str = "en"
    is_birthday: bool = False
    pronunciation: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "membership_number": self.membership_number,
            "preferred_language": self.preferred_language,
            "is_birthday": self.is_birthday,
            "pronunciation": self.pronunciation,
        }


def _mod_label(mod: str) -> str:
    if mod in menu.MILK_ALTS:
        return menu.MILK_ALTS[mod]
    if mod in menu.MODIFIERS:
        return menu.MODIFIERS[mod][0]
    return mod


class OrderState:
    """Mutable order for the current customer. Reset between customers."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.lines: list[OrderLine] = []
        self.member: Member | None = None
        self.max_drinks: int = 0                   # high-water DRINK count -> upsell window
        self.birthday_drink_id: str | None = None  # sticky member choice; survives update_cart
        self.upsell_hint_given: bool = False       # ONE upsell attempt per order
        self.nudges_surfaced: set[str] = set()     # one-shot promo nudges / notes
        self.lookup_failures: int = 0              # 2 strikes -> continue as guest
        self.confirmed: bool = False
        self._seq: int = 0

    # ---- declarative replace: the model always sends the COMPLETE cart ----
    def set_items(self, items: list[dict]) -> None:
        """Rebuild the order from a validated list of {item_id, qty, size, modifiers}.
        Declarative => modifying an item never duplicates it (UI == model's view)."""
        new: list[OrderLine] = []
        for it in items:
            item_id = it["item_id"]
            qty = max(1, int(it.get("qty", 1) or 1))
            if menu.is_drink(item_id):
                size = it.get("size") if it.get("size") in menu.SIZES else menu.DEFAULT_SIZE
                mods = [m for m in (it.get("modifiers") or []) if m in menu.MODIFIERS or m in menu.MILK_ALTS]
                new.append(OrderLine("drink", item_id, menu.display_name(item_id), qty,
                                     menu.drink_price(item_id, size, mods), size, mods))
            elif menu.is_pastry(item_id):
                new.append(OrderLine("pastry", item_id, menu.display_name(item_id), qty,
                                     menu.pastry_price(item_id), None, []))
        self.lines = new
        self.max_drinks = max(self.max_drinks, self.drink_count)

    def summary_text(self) -> str:
        if not self.lines:
            return "empty"
        parts = []
        for ln in self.lines:
            sz = f"{menu.SIZES[ln.size][0]} " if ln.size else ""
            mods = (" with " + ", ".join(_mod_label(m) for m in ln.modifiers)) if ln.modifiers else ""
            qty = f"{ln.qty} " if ln.qty != 1 else ""
            price = (f" (£{ln.unit_price:.2f}{' each' if ln.qty != 1 else ''}"
                     f"{' — FREE, offer applied' if ln.discount >= ln.unit_price else ''})")
            parts.append(f"{qty}{sz}{ln.name}{mods}{price}")
        return "; ".join(parts)

    # ---- derived ----
    @property
    def drink_count(self) -> int:
        return sum(ln.qty for ln in self.lines if ln.kind == "drink")

    @property
    def pastry_count(self) -> int:
        return sum(ln.qty for ln in self.lines if ln.kind == "pastry")

    @property
    def should_upsell(self) -> bool:
        """Upsell window: open while the order holds only its first 1-2 drinks (high-water)."""
        return (not self.confirmed) and 0 < self.max_drinks <= menu.UPSELL_FIRST_N

    def _has_upsellable_drink(self) -> bool:
        # a PAID drink with headroom — never upsell a free (birthday) drink with money quotes
        return any(ln.kind == "drink" and ln.discount < ln.unit_price
                   and (ln.size != "L" or "extra_shot" not in ln.modifiers)
                   for ln in self.lines)

    def _once(self, name: str) -> bool:
        if name in self.nudges_surfaced:
            return False
        self.nudges_surfaced.add(name)
        return True

    def claim_offer_signals(self) -> tuple[str | None, str | None]:
        """(immediate, deferred) offer signals for this update_cart turn, marked consumed.
        immediate -> deliver right after confirming the item: celebrate_pastry | upsell.
        deferred  -> deliver with the next 'anything else?': choose_pastry | one_more_drink | second_drink.
        Each fires once per order; never two in one beat."""
        drinks, pastries = self.drink_count, self.pastry_count
        immediate = deferred = None
        if drinks >= 2 and pastries >= 1 and self._once("celebrate_pastry"):
            immediate = "celebrate_pastry"
        elif self.should_upsell and not self.upsell_hint_given and self._has_upsellable_drink():
            self.upsell_hint_given = True
            immediate = "upsell"
        if drinks >= 2 and pastries == 0 and self._once("choose_pastry"):
            deferred = "choose_pastry"
        elif drinks == 1 and pastries >= 1 and self._once("one_more_drink"):
            deferred = "one_more_drink"
        elif drinks == 1 and pastries == 0 and self._once("second_drink"):
            deferred = "second_drink"
        return immediate, deferred

    @property
    def subtotal(self) -> float:
        return round(sum(ln.unit_price * ln.qty for ln in self.lines), 2)

    @property
    def discount_total(self) -> float:
        return round(sum(ln.discount for ln in self.lines), 2)

    @property
    def total(self) -> float:
        return round(self.subtotal - self.discount_total, 2)

    # ---- data-channel envelope (versioned; kiosk renders latest snapshot) ----
    def to_envelope(self, kind: str = "order_state") -> dict:
        self._seq += 1
        return {
            "type": kind,
            "ts": time.time(),
            "seq": self._seq,
            "payload": {
                "lines": [ln.to_dict() for ln in self.lines],
                "member": self.member.to_dict() if self.member else None,
                "subtotal": self.subtotal,
                "discount_total": self.discount_total,
                "total": self.total,
                "confirmed": self.confirmed,
            },
        }


def apply_promotions(order: OrderState) -> list[str]:
    """Apply the two locked promos (plan §9a). Idempotent: clears prior promo discounts first.
    (1) 2 drinks -> cheapest pastry free.  (2) member birthday -> their chosen drink free
    (cheapest drink when no choice was made or the choice left the cart).
    Returns human-readable notes for the spoken confirmation.
    """
    notes: list[str] = []
    # reset promo discounts (keep any non-promo discounts = none today)
    for ln in order.lines:
        if ln.discount_reason in (menu.PROMO_TWO_DRINKS_FREE_PASTRY, menu.PROMO_BIRTHDAY_FREE_DRINK):
            ln.discount = 0.0
            ln.discount_reason = None

    # (1) two drinks -> cheapest pastry free
    pastries = [ln for ln in order.lines if ln.kind == "pastry"]
    if order.drink_count >= 2 and pastries:
        cheapest = min(pastries, key=lambda ln: ln.unit_price)
        cheapest.discount = cheapest.unit_price          # one unit free
        cheapest.discount_reason = menu.PROMO_TWO_DRINKS_FREE_PASTRY
        notes.append(f"free {cheapest.name} (2 drinks offer)")

    # (2) birthday -> the member's chosen drink free; cheapest if no (valid) choice
    drinks = [ln for ln in order.lines if ln.kind == "drink"]
    if order.member and order.member.is_birthday and drinks:
        chosen = [ln for ln in drinks if ln.item_id == order.birthday_drink_id]
        target = (max(chosen, key=lambda ln: ln.unit_price) if chosen
                  else min(drinks, key=lambda ln: ln.unit_price))
        target.discount = round(target.discount + target.unit_price, 2)
        target.discount_reason = menu.PROMO_BIRTHDAY_FREE_DRINK
        notes.append(f"free {target.name} (birthday treat)")

    return notes
