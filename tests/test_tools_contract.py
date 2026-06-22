"""Pin the OrderState->tools offer-signal contract: every signal claim_offer_signals() can
emit must have a guidance text in tools._OFFER_SIGNALS (a missing key would KeyError live,
mid-demo). Imports agent.tools, so livekit must be installed (it is, per the other suites)."""
from agent import tools
from agent.order_state import OrderState


def item(item_id, qty=1, size="M", modifiers=None):
    return {"item_id": item_id, "qty": qty, "size": size, "modifiers": modifiers or []}


def test_every_claimable_signal_has_text():
    seen: set[str] = set()
    # Walk the carts that exercise every branch of claim_offer_signals.
    for cart in ([item("latte")],                                      # upsell + second_drink
                 [item("cookie"), item("latte")],                      # one_more_drink
                 [item("latte"), item("cappuccino")],                  # choose_pastry
                 [item("latte"), item("cappuccino"), item("cookie")]):  # celebrate_pastry
        o = OrderState()
        o.set_items(cart)
        seen.update(s for s in o.claim_offer_signals() if s)
    assert seen == set(tools._OFFER_SIGNALS), (
        f"signals without guidance text: {seen - set(tools._OFFER_SIGNALS)}; "
        f"texts never claimable: {set(tools._OFFER_SIGNALS) - seen}")


def test_lang_aliases_resolve_to_supported_codes():
    """Every set_language alias maps to a supported language code, and each code maps to itself."""
    from agent.menu import LANGUAGES
    for code in LANGUAGES:
        assert tools._LANG_ALIASES.get(code) == code           # the bare code is always accepted
    assert set(tools._LANG_ALIASES.values()) <= set(LANGUAGES)  # no alias points at an unsupported code
