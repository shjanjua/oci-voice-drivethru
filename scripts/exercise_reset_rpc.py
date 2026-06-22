"""Operator-style smoke probe: join the booth room and fire the `reset` RPC at the live agent.

Exercises the deployed per-customer reset path end-to-end (update_agent + remote-context purge +
attract publish) without touching the kiosk. Joins as an operator-* STANDARD participant — the
agent's RoomInput is pinned to the kiosk identity, so this never steals the booth mic.

Run on the VM:  cd ~/voice-order && .venv/bin/python scripts/exercise_reset_rpc.py
Then check:     journalctl -u voicedt-agent -n 30 --output cat   # expect 'reset_session: clean slate'
                # plus 'context purge: deleted N leftover remote item(s)' iff a conversation preceded it
"""
import asyncio
import os

from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv(".env")

ROOM = os.getenv("ROOM_NAME", "booth")
WS_URL = os.getenv("LIVEKIT_WS_URL", "ws://127.0.0.1:7880")
IDENTITY = "operator-reset-probe"


async def main() -> None:
    token = (api.AccessToken(os.getenv("LIVEKIT_API_KEY", "devkey"),
                             os.getenv("LIVEKIT_API_SECRET", "devsecret"))
             .with_identity(IDENTITY).with_name(IDENTITY)
             .with_grants(api.VideoGrants(room_join=True, room=ROOM)).to_jwt())
    room = rtc.Room()
    await room.connect(WS_URL, token)
    try:
        agents = []
        for _ in range(30):                  # cold room: job dispatch + ctx.connect takes a few s
            await asyncio.sleep(0.5)
            agents = [p for p in room.remote_participants.values()
                      if (p.identity or "").startswith("agent")]
            if not agents:                   # fall back: anything that isn't a kiosk/operator
                agents = [p for p in room.remote_participants.values()
                          if not (p.identity or "").startswith(("kiosk", "operator"))]
            if agents:
                break
        if not agents:
            raise SystemExit(f"no agent participant in room '{ROOM}' — is voicedt-agent up?")
        target = agents[0].identity
        resp = await room.local_participant.perform_rpc(
            destination_identity=target, method="reset", payload="")
        print(f"reset RPC -> {target}: {resp}")
    finally:
        await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
