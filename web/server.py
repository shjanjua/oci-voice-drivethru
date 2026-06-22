"""Web backend (voicedt-web): LiveKit token mint + serves the kiosk and QR sign-up.

Routes (mounted under /voice-drivethru by nginx in prod; at / locally):
  GET  /                 -> kiosk UI (frontend/)
  GET  /signup           -> QR membership page (qr_signup/)
  POST /api/token        -> mint a LiveKit join token for a kiosk room
  GET  /api/menu         -> menu JSON for the kiosk
  POST /api/signup       -> create a loyalty member (Oracle ADB)
  GET  /api/healthz      -> liveness probe
The agent worker auto-dispatches into rooms (livekit dev/start mode).
"""
from __future__ import annotations

import pathlib
import random
import re
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from livekit import api
from livekit.protocol.room import RoomConfiguration
from pydantic import BaseModel

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent import menu  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.db import get_repo  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
app = FastAPI(title="voicedt-web")

# Multi-user: each kiosk TAB joins its OWN unique room `booth-<uuid>`. This regex keeps a token from
# being minted for an arbitrary room name. NOT an access boundary — room ids are unguessable client
# UUIDs; this just prevents a stray kiosk landing in a non-booth room. (lowercase hex/uuid + the
# http-fallback's base36 id both match.)
_KIOSK_ROOM_RE = re.compile(r"^booth(-[a-z0-9-]+)?$")
# Ephemeral-room cleanup BACKSTOP. The PRIMARY teardown is the agent's kiosk-departure handler
# (supervisor._schedule_teardown); this catches an ORPHANED room whose agent died before it could run.
# empty_timeout closes a TRULY-empty room; departure_timeout is the grace before a dropped participant
# is removed — kept >= a page-reload round-trip so a reload rejoins the SAME room.
_ROOM_EMPTY_TIMEOUT = 120
_ROOM_DEPARTURE_TIMEOUT = 20


class TokenReq(BaseModel):
    room: str = "booth"
    identity: str = "kiosk"
    name: str = "Booth Kiosk"


@app.get("/api/healthz")
async def healthz():
    return {"ok": True}


@app.post("/api/token")
async def token(req: TokenReq):
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise HTTPException(500, "LiveKit API key/secret not configured")
    if not _KIOSK_ROOM_RE.match(req.room):   # a kiosk may only join a `booth`/`booth-<id>` room
        raise HTTPException(400, "invalid room")
    grant = api.VideoGrants(room_join=True, room=req.room, can_publish=True,
                            can_subscribe=True, can_publish_data=True)
    jwt = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(req.identity)
        .with_name(req.name)
        .with_grants(grant)
        .with_room_config(RoomConfiguration(
            empty_timeout=_ROOM_EMPTY_TIMEOUT, departure_timeout=_ROOM_DEPARTURE_TIMEOUT))
        .to_jwt()
    )
    return {"token": jwt, "url": settings.livekit_public_url or settings.livekit_url, "room": req.room}


@app.get("/api/menu")
async def get_menu():
    return JSONResponse({
        "drinks": [{"id": d.id, "name": d.name, "base_price": d.base_price,
                    "out_of_stock": d.id == settings.out_of_stock} for d in menu.DRINKS.values()],
        "sizes": [{"code": c, "name": n, "surcharge": s} for c, (n, s) in menu.SIZES.items()],
        "pastries": [{"id": p.id, "name": p.name, "price": p.price,
                      "out_of_stock": p.id == settings.out_of_stock} for p in menu.PASTRIES.values()],
        "milk_alts": menu.MILK_ALTS,
        "modifiers": {k: v[0] for k, v in menu.MODIFIERS.items()},
    })


class SignupReq(BaseModel):
    name: str
    pronunciation: str = ""
    language: str = "en"


@app.post("/api/signup")
async def signup(req: SignupReq):
    repo = await get_repo(settings)
    if repo is None:
        raise HTTPException(503, "membership unavailable")
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    for _ in range(8):                       # assign a unique 4-digit code, retry on collision
        code = f"{random.randint(1000, 9999)}"
        try:
            await repo.create_member(name=name, code=code, pronunciation=req.pronunciation,
                                     language=req.language, dob_today=True)
            return {"membership_number": code, "name": name}
        except Exception as e:               # noqa: BLE001
            if "ORA-00001" in str(e):
                continue                      # code collision -> try another
            raise HTTPException(500, str(e)[:120])
    raise HTTPException(500, "could not assign a membership code")


# --- page routes (serve index.html for each app; assets mounted below) ---
# The booth kiosk must ALWAYS load the latest build — never a heuristically-cached copy — so the page
# is served no-store. (The app shell is a single self-contained index.html with inline JS/CSS.)
_NO_STORE = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


def _page(subdir: str, *, no_cache: bool = False):
    f = ROOT / subdir / "index.html"
    if not f.exists():
        return JSONResponse({"error": f"{subdir} not built yet"}, status_code=503)
    return FileResponse(f, headers=_NO_STORE if no_cache else None)


@app.get("/")
async def kiosk():
    return _page("frontend", no_cache=True)


@app.get("/signup")
async def signup_page():
    return _page("qr_signup")


# static assets (mounted last so /api/* and pages take precedence)
for _name in ("frontend", "qr_signup"):
    _dir = ROOT / _name
    _dir.mkdir(exist_ok=True)
    app.mount(f"/{_name}", StaticFiles(directory=_dir), name=_name)
