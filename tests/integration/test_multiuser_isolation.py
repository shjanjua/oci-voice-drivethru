"""Layer B — opt-in real-stack proof of per-tab isolation + teardown (no browser).

Acts as TWO synthetic kiosks via the livekit `rtc` SDK against a LIVE local stack, proving that two
unique rooms get two independent agents whose audio/data never cross, and that one kiosk leaving tears
down ONLY its own room (freeing that job) without touching the other.

Run explicitly (excluded from the default suite — needs the stack up + OCI keys):
    # terminal 1: livekit-server --dev
    # terminal 2: KIOSK_TEARDOWN_GRACE=5 uv run python -m agent.main start
    # terminal 3: uv run uvicorn web.server:app --host 127.0.0.1 --port 7871
    VOICEDT_RUN_LIVE=1 uv run python -m pytest tests/integration/test_multiuser_isolation.py -m integration -v

Env knobs: VOICEDT_WEB_URL (default http://127.0.0.1:7871), LIVEKIT_HTTP_URL (default
http://127.0.0.1:7880), LIVEKIT_API_KEY/SECRET (default devkey/secret), VOICEDT_TEARDOWN_WAIT (default
50s — set KIOSK_TEARDOWN_GRACE low on the agent so this resolves fast).
"""
import asyncio
import contextlib
import os

import aiohttp
import pytest
from livekit import api, rtc

_RUN = os.getenv("VOICEDT_RUN_LIVE")
_WEB = os.getenv("VOICEDT_WEB_URL", "http://127.0.0.1:7871")
_LK_HTTP = os.getenv("LIVEKIT_HTTP_URL", "http://127.0.0.1:7880")
_LK_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
_LK_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _RUN, reason="set VOICEDT_RUN_LIVE=1 with livekit-server --dev + agent + web running"),
]


async def _mint(http: aiohttp.ClientSession, room: str, identity: str = "kiosk") -> dict:
    async with http.post(f"{_WEB}/api/token", json={"room": room, "identity": identity, "name": "test"}) as r:
        r.raise_for_status()
        return await r.json()


async def _connect(tok: dict) -> rtc.Room:
    room = rtc.Room()
    await room.connect(tok["url"], tok["token"])
    return room


async def _wait_for_agent(room: rtc.Room, timeout: float = 20.0) -> str:
    for _ in range(int(timeout / 0.3)):
        for ident in list(room.remote_participants.keys()):
            if ident.startswith("agent"):
                return ident
        await asyncio.sleep(0.3)
    raise AssertionError(f"no agent dispatched into {room.name!r} within {timeout}s")


async def _wait_room_absent(lk: "api.LiveKitAPI", name: str, timeout: float) -> bool:
    for _ in range(int(timeout / 0.5)):
        rooms = await lk.room.list_rooms(api.ListRoomsRequest())
        if name not in {r.name for r in rooms.rooms}:
            return True
        await asyncio.sleep(0.5)
    return False


async def test_two_rooms_get_distinct_isolated_agents():
    async with aiohttp.ClientSession() as http:
        a = await _connect(await _mint(http, "booth-itA"))
        b = await _connect(await _mint(http, "booth-itB"))
        try:
            ag_a = await _wait_for_agent(a)
            ag_b = await _wait_for_agent(b)
            assert ag_a != ag_b                       # a DISTINCT agent was dispatched per room

            seen_a: list = []

            def _on_data(*args):                      # tolerate DataPacket(single) or (data,part,..) forms
                pkt = args[0]
                part = getattr(pkt, "participant", None) or (args[1] if len(args) > 1 else None)
                seen_a.append(getattr(part, "identity", None))

            a.on("data_received", _on_data)
            # press-to-start both booths; each agent publishes order data ONTO ITS OWN room
            await a.local_participant.perform_rpc(destination_identity=ag_a, method="begin", payload="")
            await b.local_participant.perform_rpc(destination_identity=ag_b, method="begin", payload="")
            await asyncio.sleep(2.5)

            assert seen_a, "expected order data on room A after begin"
            assert all(src == ag_a for src in seen_a)  # everything A saw came from A's OWN agent...
            assert ag_b not in seen_a                  # ...and NOTHING from B's agent crossed over
        finally:
            with contextlib.suppress(Exception):
                await a.disconnect()
            with contextlib.suppress(Exception):
                await b.disconnect()


async def test_kiosk_departure_tears_down_only_its_room():
    async with aiohttp.ClientSession() as http:
        a = await _connect(await _mint(http, "booth-tdA"))
        b = await _connect(await _mint(http, "booth-tdB"))
        lk = api.LiveKitAPI(_LK_HTTP, _LK_KEY, _LK_SECRET)
        try:
            await _wait_for_agent(a)
            await _wait_for_agent(b)
            await b.disconnect()                      # tester B closes their tab for good
            wait = float(os.getenv("VOICEDT_TEARDOWN_WAIT", "50"))
            gone = await _wait_room_absent(lk, "booth-tdB", timeout=wait)
            names = {r.name for r in (await lk.room.list_rooms(api.ListRoomsRequest())).rooms}
            assert gone and "booth-tdB" not in names  # B's room torn down (job + socket freed)
            assert "booth-tdA" in names               # A's room/agent untouched by B leaving
        finally:
            await lk.aclose()
            with contextlib.suppress(Exception):
                await a.disconnect()
