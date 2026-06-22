"""Worker entrypoint — delegates to the SessionSupervisor.

The supervisor owns the AgentSession (one OCI cascade brain: OCI Speech STT -> OCI GenAI LLM ->
OCI Grok TTS), per-customer reset, runtime language switch, press-to-start, walk-off, and per-tab
room teardown.

Run: `python -m agent.main console|dev|start`.
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli

from .config import settings
from .db import get_repo
from .order_state import OrderState
from .supervisor import SessionSupervisor
from .userdata import UserData

load_dotenv(".env")
logger = logging.getLogger("voice-order")


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    ud = UserData(order=OrderState(), out_of_stock=settings.out_of_stock, room=ctx.room)
    ud.db = await get_repo(settings)   # None => guest mode (ordering still works)
    supervisor = SessionSupervisor(ctx, settings, ud)
    await supervisor.start()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
