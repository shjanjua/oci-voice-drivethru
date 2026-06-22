"""Pure-logic tests for the declarative OrderState + promotions (no LiveKit/network)."""
from agent import menu
from agent.order_state import Member, OrderState, apply_promotions


def item(item_id, qty=1, size="M", modifiers=None):
    return {"item_id": item_id, "qty": qty, "size": size, "modifiers": modifiers or []}


def test_set_items_prices_and_counts():
    o = OrderState()
    o.set_items([item("latte", size="L"), item("cookie")])
    assert len(o.lines) == 2
    latte = next(l for l in o.lines if l.item_id == "latte")
    assert latte.size == "L"
    assert latte.unit_price == menu.drink_price("latte", "L", [])
    assert o.total == round(latte.unit_price + menu.pastry_price("cookie"), 2)


def test_modify_does_not_duplicate():
    """The reported bug: modifying a drink must NOT add a second line."""
    o = OrderState()
    o.set_items([item("latte")])                              # 1 plain latte
    o.set_items([item("latte", modifiers=["oat"])])          # 'make it oat' -> full cart resent
    assert len(o.lines) == 1                                  # still ONE drink, not two
    assert o.lines[0].modifiers == ["oat"]
    assert o.drink_count == 1


def test_upsell_first_two_drinks_then_stop():
    o = OrderState()
    o.set_items([item("cookie")])
    assert not o.should_upsell                               # pastry only -> nothing to upsell
    o.set_items([item("cookie"), item("flat_white")])
    assert o.should_upsell                                   # first drink
    o.set_items([item("cookie"), item("flat_white"), item("muffin")])
    assert o.should_upsell                                   # pastries don't close the window
    o.set_items([item("cookie"), item("flat_white"), item("muffin"), item("cappuccino")])
    assert o.should_upsell                                   # second drink -> still open
    o.set_items([item("cookie"), item("flat_white"), item("muffin"), item("cappuccino"),
                 item("americano")])
    assert not o.should_upsell                               # 3 drinks -> stop (high-water)


def test_birthday_chosen_drink_honored():
    o = OrderState()
    o.member = Member(name="James", is_birthday=True)
    o.set_items([item("latte"), item("americano")])          # americano is cheaper
    o.birthday_drink_id = "latte"
    apply_promotions(o)
    latte = next(l for l in o.lines if l.item_id == "latte")
    americano = next(l for l in o.lines if l.item_id == "americano")
    assert latte.discount == latte.unit_price                # the CHOSEN drink, not the cheapest
    assert americano.discount == 0.0


def test_birthday_chosen_drink_fallback_to_cheapest():
    o = OrderState()
    o.member = Member(name="James", is_birthday=True)
    o.birthday_drink_id = "latte"
    o.set_items([item("americano")])                         # chosen drink not in the cart
    apply_promotions(o)
    assert o.lines[0].discount == o.lines[0].unit_price      # cheapest fallback
    o.set_items([item("americano"), item("latte")])          # choice re-added -> discount moves back
    apply_promotions(o)
    latte = next(l for l in o.lines if l.item_id == "latte")
    americano = next(l for l in o.lines if l.item_id == "americano")
    assert latte.discount == latte.unit_price
    assert americano.discount == 0.0


def test_birthday_choice_survives_cart_updates():
    o = OrderState()
    o.member = Member(name="James", is_birthday=True)
    o.set_items([item("latte"), item("americano")])
    o.birthday_drink_id = "latte"
    o.set_items([item("latte", modifiers=["oat"]), item("americano"), item("cookie")])
    apply_promotions(o)
    latte = next(l for l in o.lines if l.item_id == "latte")
    assert latte.discount == latte.unit_price                # sticky across declarative resends


def test_claim_offer_upsell_then_deferred_nudge():
    o = OrderState()
    o.set_items([item("latte")])
    assert o.claim_offer_signals() == ("upsell", "second_drink")
    assert o.claim_offer_signals() == (None, None)           # both one-shot


def test_claim_offer_qualified_choose_pastry():
    o = OrderState()
    o.set_items([item("latte"), item("cappuccino")])
    immediate, deferred = o.claim_offer_signals()
    assert deferred == "choose_pastry"                       # qualified, no pastry yet
    assert immediate == "upsell"                             # window still open -> two beats


def test_claim_offer_celebrate_once():
    o = OrderState()
    o.set_items([item("latte"), item("cappuccino"), item("cookie")])
    immediate, _ = o.claim_offer_signals()
    assert immediate == "celebrate_pastry"
    assert o.claim_offer_signals()[0] != "celebrate_pastry"  # never twice


def test_claim_offer_one_more_drink():
    o = OrderState()
    o.set_items([item("cookie"), item("latte")])
    immediate, deferred = o.claim_offer_signals()
    assert immediate == "upsell"
    assert deferred == "one_more_drink"


def test_no_upsell_when_nothing_upsellable():
    o = OrderState()
    o.set_items([item("latte", size="L", modifiers=["extra_shot"])])
    immediate, deferred = o.claim_offer_signals()
    assert immediate is None                                 # maxed out -> hint not wasted
    assert deferred == "second_drink"


def test_no_upsell_on_free_birthday_drink():
    o = OrderState()
    o.member = Member(name="James", is_birthday=True)
    o.set_items([item("latte")])
    apply_promotions(o)                                      # latte is now FREE
    immediate, deferred = o.claim_offer_signals()
    assert immediate is None                                 # never quote money on a free drink
    assert deferred == "second_drink"


def test_offer_state_resets_with_order():
    o = OrderState()
    o.member = Member(name="James", is_birthday=True)
    o.set_items([item("latte")])
    o.birthday_drink_id = "latte"
    o.claim_offer_signals()
    o.lookup_failures = 2
    o.reset()
    assert o.nudges_surfaced == set() and not o.upsell_hint_given
    assert o.birthday_drink_id is None and o.lookup_failures == 0 and o.max_drinks == 0


def test_member_pronunciation_in_dict():
    m = Member(name="James Okafor", pronunciation="JAYMS oh-KAH-for")
    assert m.to_dict()["pronunciation"] == "JAYMS oh-KAH-for"


def test_two_drinks_free_pastry():
    o = OrderState()
    o.set_items([item("latte"), item("cappuccino"), item("cookie")])
    notes = apply_promotions(o)
    cookie = next(l for l in o.lines if l.item_id == "cookie")
    assert cookie.discount == cookie.unit_price
    assert any("cookie" in n.lower() for n in notes)


def test_birthday_free_drink():
    o = OrderState()
    o.member = Member(name="James", is_birthday=True)
    o.set_items([item("latte")])
    apply_promotions(o)
    latte = next(l for l in o.lines if l.item_id == "latte")
    assert latte.discount == latte.unit_price


def test_promotions_idempotent():
    o = OrderState()
    o.set_items([item("latte"), item("cappuccino"), item("cookie")])
    apply_promotions(o)
    t1 = o.total
    apply_promotions(o)
    assert o.total == t1


def test_summary_text():
    o = OrderState()
    o.set_items([item("latte", size="L", modifiers=["oat"])])
    s = o.summary_text()
    assert "Large" in s and "Latte" in s and "Oat" in s


def test_birthday_discount_shows_live():
    """Bug 1: after lookup (member set) + adding a drink, the discount must be applied LIVE."""
    o = OrderState()
    o.member = Member(name="James", is_birthday=True)
    o.set_items([item("latte")])      # update_cart flow
    apply_promotions(o)               # now applied on every mutation, not only at confirm
    assert o.lines[0].discount == o.lines[0].unit_price
    assert o.discount_total > 0


def test_two_drinks_free_pastry_shows_live():
    """Bug 2: the free pastry shows during ordering, so the model needn't confirm to apply it."""
    o = OrderState()
    o.set_items([item("latte"), item("cappuccino"), item("cookie")])
    apply_promotions(o)
    assert o.discount_total > 0


def test_out_of_stock_has_alternative():
    assert menu.OUT_OF_STOCK_DEFAULT in menu.OOS_ALTERNATIVE


def test_envelope_shape():
    o = OrderState()
    o.set_items([item("latte")])
    env = o.to_envelope()
    assert env["type"] == "order_state" and env["seq"] == 1
    assert env["payload"]["total"] == o.total


def test_envelope_carries_discount_during_ordering():
    """Bug 1: the snapshot the kiosk renders must carry the discount mid-order (not only at confirm)."""
    o = OrderState()
    o.member = Member(name="James", is_birthday=True)
    o.set_items([item("latte")])
    apply_promotions(o)                      # update_cart now does this before publishing
    env = o.to_envelope()
    assert env["payload"]["discount_total"] > 0
    assert any(l["discount"] > 0 for l in env["payload"]["lines"])
