"""Function tools for the barista agent.

DECLARATIVE cart: the model sends the COMPLETE current order on every change
(update_cart), so the kiosk and the model can never diverge (no add-vs-modify
duplication). Return strings are guidance for the model, not spoken verbatim.
"""
from __future__ import annotations

from livekit.agents import RunContext, function_tool
from pydantic import BaseModel, Field

from . import data_channel, menu
from .order_state import Member, apply_promotions
from .userdata import UserData


class CartItem(BaseModel):
    item_id: str = Field(description="menu id, e.g. flat_white, cappuccino, americano, latte, "
                                     "cortado, hot_chocolate, chocolate_brownie, cookie, banana_bread, muffin")
    qty: int = Field(default=1, description="quantity (>=1)")
    size: str = Field(default="M", description="'S','M','L' for drinks; ignored for pastries")
    modifiers: list[str] = Field(default_factory=list,
                                 description="any of iced, extra_shot, decaf, and one milk alternative "
                                             "(soya, oat, coconut, almond, semi_skimmed)")


async def _publish(ud: UserData, kind: str = "order_state") -> None:
    if ud.room is not None:
        await data_channel.publish(ud.room, ud.order.to_envelope(kind))


# Texts for OrderState.claim_offer_signals() — the ONLY trigger for proactive offers.
# "immediate" = say right after confirming the item; "deferred" = ride the next "anything else?".
_OFFER_SIGNALS = {
    "upsell":           ("OFFER SIGNAL immediate (once per order): offer ONE upsell on their drink — "
                         "a larger size or an extra shot. NOT a pastry."),
    "celebrate_pastry": ("OFFER SIGNAL immediate (mention once): their free pastry discount just "
                         "applied — celebrate briefly, e.g. 'that pastry's on us'."),
    "choose_pastry":    ("OFFER SIGNAL deferred (with your next 'anything else?' — or just before the "
                         "readback if they're already done): they have QUALIFIED for a free pastry — "
                         "tell them and ask which pastry they'd like. (If the order ends up with several "
                         "pastries, the CHEAPEST one is the free one — don't promise otherwise.)"),
    "one_more_drink":   ("OFFER SIGNAL deferred (with your next 'anything else?' — or just before the "
                         "readback if they're already done): one more drink and their pastry is FREE."),
    "second_drink":     ("OFFER SIGNAL deferred (with your next 'anything else?' — or just before the "
                         "readback if they're already done): add a second drink and they get a FREE pastry."),
}


@function_tool
async def update_cart(ctx: RunContext[UserData], items: list[CartItem]) -> str:
    """Set the customer's ENTIRE current order. Call this on EVERY change — adding, modifying,
    or removing — passing the COMPLETE list of items they currently want (not just the change).
    To clear the order, pass an empty list. Modifying an item (e.g. changing milk or size) means
    sending that item again with its new size/modifiers in the full list. The result may include
    OFFER SIGNALS — an 'immediate' one to deliver right after confirming the item, and/or a
    'deferred' one to deliver with your next 'anything else?' question. Otherwise make NO
    proactive offers."""
    ud = ctx.userdata
    valid: list[dict] = []
    oos: list[str] = []
    unknown: list[str] = []
    for it in items:
        if it.qty <= 0:
            continue
        if it.item_id == ud.out_of_stock:
            oos.append(it.item_id)
            continue
        if menu.is_drink(it.item_id) or menu.is_pastry(it.item_id):
            valid.append({"item_id": it.item_id, "qty": it.qty, "size": it.size, "modifiers": it.modifiers})
        else:
            unknown.append(it.item_id)

    ud.order.set_items(valid)
    apply_promotions(ud.order)   # apply offers LIVE so discounts show immediately (no need to confirm)
    await _publish(ud)

    notes: list[str] = []
    for o in oos:
        alt = menu.OOS_ALTERNATIVE.get(o)
        notes.append(f"{menu.display_name(o)} is OUT OF STOCK — apologise and suggest "
                     f"{menu.display_name(alt) if alt else 'an alternative'}")
    for u in unknown:
        notes.append(f"'{u}' is not on the menu")

    msg = (f"Order is now: {ud.order.summary_text()}. Running total £{ud.order.total:.2f} "
           f"(shown live on the customer's screen — do NOT read the running total aloud).")
    if ud.order.discount_total > 0:
        msg += (f" (Offers already applied automatically — £{ud.order.discount_total:.2f} off. "
                f"Do NOT call confirm_order just to apply a discount.)")
    if (ud.order.member and ud.order.member.is_birthday and ud.order.birthday_drink_id
            and ud.order.drink_count > 0
            and not any(ln.item_id == ud.order.birthday_drink_id for ln in ud.order.lines)
            and ud.order._once("birthday_fallback")):
        msg += (" NOTE: their chosen birthday drink is no longer in the order — if they swapped it "
                "for another drink, ask whether to move the free birthday drink to it (then call "
                "set_birthday_drink); otherwise it now applies to the cheapest drink, mention this briefly.")
    if notes:
        msg += " NOTE: " + "; ".join(notes) + "."
    immediate, deferred = ud.order.claim_offer_signals()
    for s in (immediate, deferred):
        if s:
            msg += " " + _OFFER_SIGNALS[s]
    return msg


@function_tool
async def lookup_member(ctx: RunContext[UserData], membership_number: str = "", name: str = "") -> str:
    """Look up a loyalty member ONCE you have their 4-digit code (preferred, confirmed digit by
    digit) or their name — if they only said they're a member, ask for the code first. Say "Let me
    look you up" while calling. The result includes the member's name (and how to pronounce it),
    preferred language, birthday status, and exactly what to say next."""
    ud = ctx.userdata
    if not membership_number.strip() and not name.strip():
        return ("NO IDENTIFIER GIVEN: ask for their 4-digit member code (or their name) first, "
                "then call lookup_member again.")
    if ud.db is None:
        return "GUEST: membership lookup isn't available right now — continue as a guest."
    member: Member | None = await ud.db.lookup_member(membership_number.strip() or None, name.strip() or None)
    if not member:
        ud.order.lookup_failures += 1
        if ud.order.lookup_failures >= 2:
            return ("NOT_FOUND (second failure): continue warmly as a GUEST now — no more lookups "
                    "unless the customer volunteers a new code later. Carry on with the order; never stall.")
        return ("NOT_FOUND: no match. Ask them to repeat the 4-digit code ONE DIGIT AT A TIME, "
                "or offer to try their name instead.")
    ud.order.member = member
    apply_promotions(ud.order)   # re-price now (e.g. birthday free drink on an already-added drink)
    await _publish(ud)
    parts = [f"MEMBER FOUND: name={member.name}. Greet them back BY NAME."]
    if member.pronunciation:
        parts.append(f'Their name is pronounced "{member.pronunciation}" — say the name once, '
                     f'naturally; NEVER read this pronunciation guide aloud as extra words.')
    lang = menu.LANGUAGES.get(member.preferred_language, member.preferred_language)
    parts.append(f"Their preferred language is {lang}: if you are NOT already speaking {lang}, offer ONCE "
                 f"— speaking in {lang} — to continue in {lang}; respect their choice either way.")
    if member.is_birthday:
        parts.append("It is their BIRTHDAY: congratulate them warmly, tell them a drink is on us today, "
                     "and ask WHICH drink they'd like as their free birthday drink — when they choose, "
                     "call set_birthday_drink (and update_cart if it isn't in the cart yet). "
                     "If you make the language offer, ask that FIRST and alone — then deliver the "
                     "birthday part on your NEXT turn, in the language they chose.")
    return " ".join(parts)


@function_tool
async def set_birthday_drink(ctx: RunContext[UserData], item_id: str) -> str:
    """Record WHICH drink a birthday member wants their free birthday drink applied to. Call when
    the member chooses; pass the drink's menu id (e.g. latte). The discount applies automatically
    and survives later cart changes — do NOT call confirm_order to apply it."""
    ud = ctx.userdata
    if not (ud.order.member and ud.order.member.is_birthday):
        return "NO BIRTHDAY REWARD: only members on their birthday get a free drink — do not promise one."
    if not menu.is_drink(item_id):
        return (f"'{item_id}' is not a drink — the birthday reward applies to drinks only. "
                f"Ask which DRINK they'd like it on.")
    if item_id == ud.out_of_stock:
        alt = menu.OOS_ALTERNATIVE.get(item_id)
        return (f"{menu.display_name(item_id)} is OUT OF STOCK — suggest "
                f"{menu.display_name(alt) if alt else 'an alternative'} for their birthday drink instead.")
    ud.order.birthday_drink_id = item_id
    apply_promotions(ud.order)
    await _publish(ud)
    if any(ln.item_id == item_id for ln in ud.order.lines):
        return (f"BIRTHDAY DRINK SET: {menu.display_name(item_id)} — the free-drink discount is "
                f"applied automatically (£{ud.order.discount_total:.2f} total off).")
    return (f"BIRTHDAY DRINK NOTED: {menu.display_name(item_id)}. It is NOT in the cart yet — add it "
            f"with update_cart and the discount will apply automatically.")


# Names the model might pass -> our 2-letter language code (menu.LANGUAGES keys).
_LANG_ALIASES = {
    "en": "en", "english": "en", "en-us": "en", "en-gb": "en",
    "es": "es", "spanish": "es", "español": "es", "espanol": "es", "castellano": "es",
    "fr": "fr", "french": "fr", "français": "fr", "francais": "fr",
    "hi": "hi", "hindi": "hi", "हिन्दी": "hi",
    "de": "de", "german": "de", "deutsch": "de",
}


@function_tool
async def set_language(ctx: RunContext[UserData], language: str) -> str:
    """Switch the conversation language. Call this the MOMENT you decide to change languages — when
    the customer explicitly asks, OR a member accepts the offer to continue in their preferred
    language. Pass a 2-letter code: 'en' English, 'es' Spanish, 'fr' French, 'hi' Hindi, 'de' German.
    After calling, speak ONLY in the new language (your next reply, all offers, the readback, the
    goodbye). Do NOT call this for a single foreign word, a name, an accent, or a menu item."""
    ud = ctx.userdata
    code = _LANG_ALIASES.get(language.strip().lower())
    if code is None:
        return (f"UNSUPPORTED LANGUAGE '{language}'. Supported: {', '.join(menu.LANGUAGES.values())}. "
                f"Stay in the current language and continue the order.")
    if callable(ud.request_language_switch):
        ud.request_language_switch(code)
    return (f"LANGUAGE SWITCHED to {menu.LANGUAGES[code]}. From now on speak ONLY in "
            f"{menu.LANGUAGES[code]} — continue the order from where you are; do not re-greet.")


@function_tool
async def confirm_order(ctx: RunContext[UserData]) -> str:
    """Finalise the order: apply offers, compute the total, and save it. Call ONLY after you have
    read the full order back (items, freebies, total) and the customer explicitly said yes.
    After this, give a short warm goodbye and STOP taking items."""
    ud = ctx.userdata
    if not ud.order.lines:
        return "EMPTY: there's nothing in the order yet."
    notes = apply_promotions(ud.order)
    ud.order.confirmed = True
    if ud.db is not None:
        try:
            await ud.db.save_order(ud.order)
        except Exception:
            pass
    await _publish(ud, kind="order_confirmed")
    if callable(ud.request_reset):
        ud.request_reset()   # supervisor resets to attract after a short goodbye window
    promo = f" Applied: {', '.join(notes)}." if notes else ""
    return (f"CONFIRMED: total £{ud.order.total:.2f}.{promo} Give a brief, warm goodbye and do NOT "
            f"take any more items unless the customer clearly starts a brand-new order.")


ALL_TOOLS = [update_cart, lookup_member, set_birthday_drink, set_language, confirm_order]
