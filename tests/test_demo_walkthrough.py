"""The 9-scenario booth walkthrough (plan §8), automated at the tool layer.

Drives the REAL function tools (update_cart / lookup_member / set_birthday_drink /
confirm_order) with a stub DB and asserts the exact return-string guidance the model
would receive at every beat — everything in the live walkthrough except voice itself.
"""
from types import SimpleNamespace

from agent import menu, tools
from agent.order_state import Member, OrderState
from agent.userdata import UserData


def james() -> Member:
    return Member(name="James Okafor", membership_number="1234", preferred_language="en",
                  is_birthday=True, pronunciation="JAYMS oh-KAH-for")


def sofia() -> Member:
    return Member(name="Sofia Marquez", membership_number="2468", preferred_language="es",
                  is_birthday=False)


class StubRepo:
    """Mimics OrderRepo.lookup_member(code, name) / save_order."""

    def __init__(self, *members: Member):
        self.by_code = {m.membership_number: m for m in members}
        self.saved: list[OrderState] = []

    async def lookup_member(self, code=None, name=None):
        if code and code in self.by_code:
            return self.by_code[code]
        if name:
            for m in self.by_code.values():
                if m.name.lower().split()[0] == name.lower().split()[0]:
                    return m
        return None

    async def save_order(self, order, session_id=""):
        self.saved.append(order)


def make_ctx(*members: Member):
    ud = UserData(order=OrderState(), out_of_stock=menu.OUT_OF_STOCK_DEFAULT,
                  db=StubRepo(*members) if members else None)
    return SimpleNamespace(userdata=ud), ud


def cart(*ids_or_tuples):
    items = []
    for it in ids_or_tuples:
        if isinstance(it, str):
            items.append(tools.CartItem(item_id=it))
        else:
            items.append(tools.CartItem(**it))
    return items


async def test_scenario_1_birthday_member_full_order():
    """James 1234: lookup -> birthday beats; chosen large latte stays free (not cheapest);
    one upsell; qualified-pastry nudge; celebrate; confirm names both freebies."""
    ctx, ud = make_ctx(james())
    r = await tools.lookup_member(ctx, membership_number="1234")
    assert "MEMBER FOUND: name=James Okafor" in r and "BY NAME" in r
    assert 'pronounced "JAYMS oh-KAH-for"' in r and "NEVER read this pronunciation guide" in r
    assert "BIRTHDAY" in r and "WHICH drink" in r and "set_birthday_drink" in r
    assert "preferred language is English" in r              # self-guarding offer line

    r = await tools.update_cart(ctx, items=cart({"item_id": "latte", "size": "L"}))
    assert "OFFER SIGNAL immediate" not in r                 # never upsell the FREE birthday drink
    assert "FREE, offer applied" in r                        # line price marks it free
    assert "OFFER SIGNAL deferred" in r and "second drink" in r

    r = await tools.set_birthday_drink(ctx, item_id="latte")
    assert r.startswith("BIRTHDAY DRINK SET: Latte")
    latte = next(ln for ln in ud.order.lines if ln.item_id == "latte")
    assert latte.discount == latte.unit_price == 4.20        # chosen L latte free

    r = await tools.update_cart(ctx, items=cart({"item_id": "latte", "size": "L"}, "americano"))
    assert "QUALIFIED for a free pastry" in r                # cheaper americano present...
    assert "OFFER SIGNAL immediate" in r and "larger size or an extra shot" in r  # upsell on the PAID drink
    assert "(£3.20)" in r                                    # spoken price comes from the tool result
    latte = next(ln for ln in ud.order.lines if ln.item_id == "latte")
    assert latte.discount == latte.unit_price               # ...but the CHOSEN latte stays free

    r = await tools.update_cart(ctx, items=cart({"item_id": "latte", "size": "L"}, "americano",
                                                "chocolate_brownie"))
    assert "celebrate" in r and "OFFER SIGNAL immediate" in r
    assert ud.order.total == 3.20                            # only the americano is paid

    r = await tools.confirm_order(ctx)
    assert r.startswith("CONFIRMED: total £3.20")
    assert "free Chocolate Brownie (2 drinks offer)" in r and "free Latte (birthday treat)" in r
    assert ud.db.saved and ud.db.saved[0] is ud.order


async def test_scenario_2_spanish_preferred_member_no_birthday():
    ctx, _ = make_ctx(sofia())
    r = await tools.lookup_member(ctx, membership_number="2468")
    assert "MEMBER FOUND: name=Sofia Marquez" in r
    assert "BIRTHDAY" not in r                               # never congratulate a non-birthday
    assert "pronounced" not in r                             # no pronunciation on file
    assert "preferred language is Spanish" in r and "offer ONCE" in r


async def test_scenario_3_guest_orders_first_membership_lands_mid_order():
    ctx, ud = make_ctx(james())
    await tools.update_cart(ctx, items=cart("cappuccino"))
    assert ud.order.member is None and ud.order.discount_total == 0
    await tools.lookup_member(ctx, membership_number="1234")
    capp = ud.order.lines[0]
    assert capp.discount == capp.unit_price                  # birthday re-priced LIVE


async def test_scenario_4_two_drinks_in_first_utterance():
    ctx, _ = make_ctx()
    r = await tools.update_cart(ctx, items=cart({"item_id": "latte", "qty": 2}))
    assert "OFFER SIGNAL immediate" in r and "OFFER SIGNAL deferred" in r
    assert "QUALIFIED" in r                                  # qty counts as 2 drinks


async def test_scenario_5_two_strike_lookup_then_guest():
    ctx, ud = make_ctx(james())
    r1 = await tools.lookup_member(ctx, membership_number="9999")
    assert "ONE DIGIT AT A TIME" in r1
    r2 = await tools.lookup_member(ctx, membership_number="0000")
    assert "GUEST" in r2 and "never stall" in r2
    assert ud.order.lookup_failures == 2
    r3 = await tools.update_cart(ctx, items=cart("latte"))   # ordering continues immediately
    assert "Order is now: Medium Latte" in r3


async def test_scenario_6_out_of_stock_paths():
    ctx, ud = make_ctx(james())
    await tools.lookup_member(ctx, membership_number="1234")
    r = await tools.update_cart(ctx, items=cart("cortado"))
    assert "OUT OF STOCK" in r and "Flat White" in r
    assert not ud.order.lines                                # OOS item never lands in the cart
    r = await tools.set_birthday_drink(ctx, item_id="cortado")
    assert "OUT OF STOCK" in r and ud.order.birthday_drink_id is None


async def test_scenario_7_single_hot_chocolate_smooth_close():
    ctx, ud = make_ctx()
    r = await tools.update_cart(ctx, items=cart("hot_chocolate"))
    assert "OFFER SIGNAL immediate" in r                     # one upsell beat
    assert "second drink" in r                               # promo still pushed
    r = await tools.confirm_order(ctx)
    assert r.startswith("CONFIRMED:") and ud.order.confirmed


async def test_scenario_8_birthday_fallback_when_chosen_drink_removed():
    ctx, ud = make_ctx(james())
    await tools.lookup_member(ctx, membership_number="1234")
    await tools.update_cart(ctx, items=cart("latte", "americano"))
    await tools.set_birthday_drink(ctx, item_id="latte")
    r = await tools.update_cart(ctx, items=cart("americano"))   # latte removed
    assert "applies to the cheapest drink" in r              # one-shot fallback note...
    assert "set_birthday_drink" in r                         # ...inviting a re-pin to the swap
    assert ud.order.lines[0].discount == ud.order.lines[0].unit_price
    r = await tools.update_cart(ctx, items=cart("americano", {"item_id": "americano", "size": "L"}))
    assert "birthday drink is no longer" not in r            # note never repeats


async def test_scenario_9_set_birthday_drink_validation():
    ctx, _ = make_ctx(sofia())
    await tools.lookup_member(ctx, membership_number="2468")
    r = await tools.set_birthday_drink(ctx, item_id="latte")
    assert "NO BIRTHDAY REWARD" in r                         # Sofia isn't a birthday member
    ctx, _ = make_ctx(james())
    await tools.lookup_member(ctx, membership_number="1234")
    r = await tools.set_birthday_drink(ctx, item_id="cookie")
    assert "not a drink" in r
