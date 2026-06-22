"""Layer C — opt-in browser E2E: each tab/device gets its OWN room; reload survives; no cross-eviction.

Drives the REAL shipped frontend (frontend/index.html) in two isolated browser contexts (= two devices)
via Playwright. The room-id + reload assertions need only the web server running (roomId() sets
sessionStorage the moment connect() builds the token body — before any LiveKit socket); the no-eviction
assertion needs the FULL stack so both pages actually connect.

Setup + run (excluded from the default suite):
    uv pip install playwright && uv run playwright install chromium
    # terminal 1: uv run uvicorn web.server:app --host 127.0.0.1 --port 7871   (web server)
    # (no-eviction test additionally needs: livekit-server --dev  +  uv run python -m agent.main start)
    VOICEDT_RUN_LIVE=1 uv run python -m pytest tests/e2e/test_kiosk_isolation.py -v
"""
import os

import pytest

async_playwright = pytest.importorskip("playwright.async_api").async_playwright

_WEB = os.getenv("VOICEDT_WEB_URL", "http://127.0.0.1:7871")

pytestmark = pytest.mark.skipif(
    not os.getenv("VOICEDT_RUN_LIVE"),
    reason="set VOICEDT_RUN_LIVE=1 with the web server running (+ full stack for no-eviction)",
)

_ROOM_KEY = "voicedt_room"


async def _press_start_and_get_room(page) -> str:
    await page.goto(_WEB)
    await page.click("#startBtn")                       # pressStart() -> connect() -> roomId() sets storage
    await page.wait_for_function(f"() => !!sessionStorage.getItem('{_ROOM_KEY}')", timeout=10_000)
    return await page.evaluate(f"sessionStorage.getItem('{_ROOM_KEY}')")


async def test_two_devices_get_distinct_rooms_and_reload_is_stable():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx_a = await browser.new_context()         # two isolated contexts = two devices
            ctx_b = await browser.new_context()
            page_a = await ctx_a.new_page()
            page_b = await ctx_b.new_page()

            id_a = await _press_start_and_get_room(page_a)
            id_b = await _press_start_and_get_room(page_b)

            assert id_a and id_b and id_a != id_b               # distinct room per device
            assert id_a.startswith("booth-") and id_b.startswith("booth-")

            await page_a.reload()                               # reload-survival: same tab keeps its room
            id_a_after = await page_a.evaluate(f"sessionStorage.getItem('{_ROOM_KEY}')")
            assert id_a_after == id_a
        finally:
            await browser.close()


async def test_second_device_does_not_evict_the_first():
    if not os.getenv("VOICEDT_FULL_STACK"):
        pytest.skip("set VOICEDT_FULL_STACK=1 with livekit-server --dev + agent running")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page_a = await (await browser.new_context()).new_page()
            page_b = await (await browser.new_context()).new_page()

            await _press_start_and_get_room(page_a)
            # A connects + greets => its idle overlay drops
            await page_a.wait_for_function(
                "() => getComputedStyle(document.getElementById('overlay')).display === 'none'",
                timeout=15_000)

            await _press_start_and_get_room(page_b)             # a SECOND device joins (its own room)
            await page_b.wait_for_timeout(2_000)

            # the OLD shared-'booth' build would have evicted A here (Disconnected -> showIdle -> overlay back).
            overlay_a = await page_a.evaluate(
                "getComputedStyle(document.getElementById('overlay')).display")
            assert overlay_a == "none"                          # A still live — no cross-eviction
        finally:
            await browser.close()
