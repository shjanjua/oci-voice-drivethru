"""SessionSupervisor — per-customer reset + runtime language switch (offline).

Stubs make_brain / AgentSession / data_channel.publish (the live session needs LiveKit + OCI + a
room) and pins the two load-bearing update_agent paths: reset (clean slate + attract, back to the
default language) and set_language (preserve the order, rebuild the brain for the new language).
"""
from types import SimpleNamespace

import pytest

from agent import supervisor as sup
from agent.config import Settings
from agent.order_state import OrderState
from agent.userdata import UserData


class FakeSession:
    def __init__(self, **kw):
        self.agents = []
        self.interrupted = 0

    def update_agent(self, agent):
        self.agents.append(agent)

    def interrupt(self):
        self.interrupted += 1

    def on(self, *_a, **_k):              # decorator no-op (only used by start(), not these tests)
        def deco(fn):
            return fn
        return deco


def _make_sup(monkeypatch):
    monkeypatch.setattr(sup, "AgentSession", lambda **kw: FakeSession(**kw))
    # fake brain captures the language + whether an order summary was threaded (fresh vs preserve)
    monkeypatch.setattr(sup, "make_brain",
                        lambda s, **kw: SimpleNamespace(lang=kw.get("lang"), summary=kw.get("order_summary")))
    published: list[dict] = []

    async def fake_publish(room, env):
        published.append(env)
    monkeypatch.setattr(sup.data_channel, "publish", fake_publish)

    s = Settings(default_language="en")
    room = SimpleNamespace(local_participant=object(), remote_participants={})
    ud = UserData(order=OrderState(), out_of_stock="cortado", room=room)
    ctx = SimpleNamespace(room=room)
    supervisor = sup.SessionSupervisor(ctx, s, ud)
    return supervisor, published, ud


def _order(ud):
    ud.order.set_items([{"item_id": "latte", "qty": 1, "size": "M", "modifiers": []}])


async def test_apply_language_switches_and_preserves_order(monkeypatch):
    supervisor, _published, ud = _make_sup(monkeypatch)
    _order(ud)
    await supervisor._apply_language("es")
    assert supervisor._language == "es"
    brain = supervisor.session.agents[-1]
    assert brain.lang == "es"            # new brain built for the new language
    assert brain.summary                 # order preserved -> re-grounding summary present
    assert ud.order.lines               # order untouched by a language switch


async def test_set_language_rejects_unsupported_and_noops_same(monkeypatch):
    supervisor, _published, _ud = _make_sup(monkeypatch)
    supervisor.set_language("xx")        # unsupported -> no swap scheduled, no state change
    supervisor.set_language("en")        # already the current language -> no-op
    assert supervisor._language == "en"
    assert supervisor.session.agents == []   # __init__ doesn't swap; neither call did either


async def test_reset_session_clears_order_and_returns_to_idle(monkeypatch):
    supervisor, published, ud = _make_sup(monkeypatch)
    _order(ud)
    supervisor._language = "es"           # mid-order in another language
    await supervisor.reset_session()
    assert ud.order.lines == []           # cleared for the next customer
    assert supervisor._language == "en"   # back to the default language
    assert supervisor.session.interrupted == 1
    assert published[-1]["type"] == "attract"
    assert supervisor.session.agents[-1].summary is None   # fresh brain (no order to carry)
