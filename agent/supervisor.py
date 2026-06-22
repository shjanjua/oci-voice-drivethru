"""SessionSupervisor — owns the ONE AgentSession (one OCI cascade brain).

Lifecycle ops on the session, all via update_agent (the only SDK-supported live swap):
  - per-customer reset (reset_session): fresh brain on an empty context + cleared order +
    'attract' (back to idle). The greeting is press-driven (begin RPC), so reset ends the booth
    in idle, ready for the next press.
  - language switch (set_language): fresh brain built for the new language (new STT language code +
    new TTS language), PRESERVING the in-progress order via an order-summary re-grounding note.
Also: press-to-start greeting, walk-off auto-reset, and per-tab room teardown (multi-user isolation).
"""
from __future__ import annotations

import asyncio
import logging
import time

from livekit.agents import AgentSession, JobContext, room_io

from . import data_channel
from .brain import make_brain
from .config import Settings
from .menu import LANGUAGES
from .userdata import UserData

logger = logging.getLogger("voice-order")

GREETING = (
    "A new customer just pressed start. In ENGLISH, in one or two short spoken sentences: welcome them "
    "warmly to the Oracle coffee bar, and ALWAYS ask whether they're a member so their rewards can be "
    "applied — for example: 'Welcome to the Oracle coffee bar! Are you a member with us, or shall I just "
    "take your order?'. Improvise the wording but keep BOTH parts: the welcome and the membership "
    "question. If they reply with an order instead of an answer, just take the order."
)
RESET_DEBOUNCE_S = 1.0        # coalesce rapid Start-over taps on the (unguarded) reset RPC
# Pin the agent's audio input to the kiosk participant. Each tab is its OWN room with exactly one
# kiosk, so this also re-links the kiosk across reloads (the kiosk uses this exact identity — see
# frontend/index.html).
KIOSK_IDENTITY = "kiosk"


class SessionSupervisor:
    def __init__(self, ctx: JobContext, s: Settings, ud: UserData):
        self.ctx = ctx
        self.s = s
        self.ud = ud
        self._language = s.default_language     # per-session brain language (switchable at runtime)
        self.session = AgentSession(userdata=ud, user_away_timeout=s.user_away_timeout)
        self._reset_task: asyncio.Task | None = None
        # Per-tab rooms (multi-user): when the kiosk leaves for good — NOT a reload — tear the room
        # down so this job's agent subprocess is freed promptly. close_on_disconnect=False keeps the
        # session alive across reloads, so nothing else ends an abandoned room.
        self._teardown_task: asyncio.Task | None = None
        # Serialize + debounce resets/swaps so the unguarded `reset` RPC can't stampede update_agent.
        self._swap_lock = asyncio.Lock()
        self._last_reset: float = 0.0
        self._room_events_wired: bool = False   # room-scoped handlers register exactly once
        ud.request_reset = self.schedule_reset
        ud.request_language_switch = self.set_language

    def _brain(self, *, fresh: bool):
        summary = None if fresh else (self.ud.order.summary_text() if self.ud.order.lines else None)
        return make_brain(self.s, lang=self._language, order_summary=summary)

    async def start(self) -> None:
        # close_on_disconnect=False keeps the session (and agent) alive when the kiosk reloads, so a
        # fresh page load re-syncs its screen via the participant_connected handler.
        await self.session.start(
            agent=self._brain(fresh=True), room=self.ctx.room,
            room_input_options=room_io.RoomInputOptions(
                close_on_disconnect=False, participant_identity=KIOSK_IDENTITY))
        self._wire_events()
        self._register_rpc()
        logger.info("supervisor started lang=%s", self._language)
        # Start silent: the kiosk shows the blurred "press to start" idle screen and only the
        # customer's press (begin RPC) triggers the greeting — so the bot never talks to an empty booth.
        await data_channel.publish(self.ctx.room, self.ud.order.to_envelope("session_start"))

    async def begin(self) -> None:
        """Kiosk 'press to start' — greet the new customer (cancelling any pending idle reset)."""
        if self._reset_task and not self._reset_task.done():
            self._reset_task.cancel()
        await self._greet()

    async def _greet(self, retries: int = 10, delay: float = 0.6) -> None:
        """generate_reply, retrying until the (possibly just-swapped) session can schedule speech."""
        for _ in range(retries):
            try:
                await self.session.generate_reply(instructions=GREETING)
                return
            except Exception:
                await asyncio.sleep(delay)
        logger.warning("greet: could not schedule speech after %d retries", retries)

    # ---- per-customer reset (CLEAR context + order) ----
    def schedule_reset(self, delay: float = 6.0) -> None:
        if self._reset_task and not self._reset_task.done():
            return

        async def _do():
            try:
                await asyncio.sleep(delay)
                await self.reset_session()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("scheduled reset failed")
        self._reset_task = asyncio.create_task(_do())

    async def reset_session(self) -> None:
        async with self._swap_lock:
            now = time.monotonic()
            if now - self._last_reset < RESET_DEBOUNCE_S:
                return    # coalesce rapid Start-over taps — the booth is already a clean slate
            self._last_reset = now
            try:
                self.session.interrupt()          # stop any in-flight goodbye speech
            except Exception:
                pass
            self.ud.order.reset()
            self._language = self.s.default_language   # next customer starts in the default language
            # A fresh brain has an empty chat context => a clean slate for the next customer.
            self.session.update_agent(self._brain(fresh=True))
            # End in idle: the kiosk blurs back to "press to start". The NEXT customer's press (begin)
            # provides the greeting — no auto re-greet to an empty booth.
            await data_channel.publish(self.ctx.room, self.ud.order.to_envelope("attract"))
            logger.info("reset_session: clean slate for next customer")

    # ---- runtime language switch (PRESERVE order) ----
    def set_language(self, lang: str) -> None:
        """Schedule a runtime language switch. SYNC (safe to call from a tool, like schedule_reset):
        the brain swap runs as a background task so it executes off the tool's turn — matching how the
        proven brain-swap path runs (off an RPC/task, not inline). update_agent without interrupt lets
        the in-flight reply finish; the new STT language + TTS language take effect for the next turn."""
        if lang not in LANGUAGES or lang == self._language:
            return
        asyncio.create_task(self._apply_language(lang))

    async def _apply_language(self, lang: str) -> None:
        try:
            async with self._swap_lock:
                if lang == self._language:
                    return
                self._language = lang
                # fresh=False => the new brain re-grounds on the in-progress order (no re-greet) and
                # rebuilds the STT (new language code) + TTS (new language).
                self.session.update_agent(self._brain(fresh=False))
            logger.info("set_language -> %s", lang)
        except Exception:
            logger.exception("language switch failed")

    # ---- events ----
    def _wire_events(self) -> None:
        # SESSION-scoped handler — armed on the session.
        try:
            @self.session.on("user_state_changed")
            def _on(ev):  # noqa: ANN001
                st = getattr(ev, "new_state", None) or getattr(ev, "state", None)
                if st == "away" and not self.ud.order.confirmed and self.ud.order.lines == []:
                    self.schedule_reset(delay=3.0)
        except Exception:
            pass
        self._wire_room_events()

    def _wire_room_events(self) -> None:
        # ROOM-scoped handlers — register exactly once (the room outlives the session).
        if self._room_events_wired:
            return
        self._room_events_wired = True
        try:
            @self.ctx.room.on("participant_connected")
            def _on_join(p):  # noqa: ANN001 — re-sync a reloaded kiosk's screen on the live session
                if (getattr(p, "identity", "") or "").startswith("kiosk"):
                    # A kiosk (re)appeared — this is a reload, so CANCEL any pending teardown the
                    # preceding disconnect scheduled; keep the session alive for the same customer.
                    if self._teardown_task and not self._teardown_task.done():
                        self._teardown_task.cancel()
                    # Restore a reloaded kiosk that was MID-ORDER. For an EMPTY order publish NOTHING:
                    # a freshly-loaded kiosk already shows the blurred idle overlay, and an 'attract'
                    # here would race the press->begin->greet handshake and re-blur the screen.
                    if self.ud.order.lines:
                        asyncio.create_task(
                            data_channel.publish(self.ctx.room, self.ud.order.to_envelope("order_state")))
        except Exception:
            pass
        try:
            @self.ctx.room.on("participant_disconnected")
            def _on_leave(p):  # noqa: ANN001 — kiosk gone => schedule a guarded room teardown
                if (getattr(p, "identity", "") or "").startswith("kiosk"):
                    self._schedule_teardown()
        except Exception:
            pass

    def _schedule_teardown(self, grace: float | None = None) -> None:
        """Free this job (agent subprocess) when the kiosk has truly left.

        A page RELOAD also fires participant_disconnected, so we wait `grace` (KIOSK_TEARDOWN_GRACE,
        well past a reload / network blip) and re-check that NO kiosk is present before deleting the
        room — robust against the reload race. Reconnect cancels the pending task (see _on_join).
        Idempotent: a second disconnect while one is pending is a no-op."""
        if self._teardown_task and not self._teardown_task.done():
            return
        if grace is None:
            grace = self.s.kiosk_teardown_grace

        async def _do():
            try:
                await asyncio.sleep(grace)
                if any((getattr(p, "identity", "") or "").startswith("kiosk")
                       for p in self.ctx.room.remote_participants.values()):
                    return                       # a reload brought the kiosk back — keep the session
                logger.info("kiosk gone — tearing down room %s", getattr(self.ctx.room, "name", "?"))
                await asyncio.shield(self.ctx.delete_room())   # ends the job; frees the subprocess
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("scheduled teardown failed")
        self._teardown_task = asyncio.create_task(_do())

    # ---- kiosk RPC ----
    def _register_rpc(self) -> None:
        lp = self.ctx.room.local_participant

        async def _run(name, coro):
            try:
                await coro()
            except Exception:
                logger.exception("rpc %s failed", name)

        def reg(name, coro):
            async def handler(data):  # noqa: ANN001 — fire-and-forget so slow ops don't time out the ack
                asyncio.create_task(_run(name, coro))
                return "ok"
            try:
                lp.register_rpc_method(name, handler)
            except Exception:
                logger.exception("register rpc %s", name)

        reg("begin", self.begin)            # kiosk "press to start" -> greet
        reg("reset", self.reset_session)    # kiosk "start over / end"
