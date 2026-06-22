"""/api/token room scoping, kiosk-room guard, and ephemeral room-config (multi-user).

Offline: we await the FastAPI route coroutine directly with a TokenReq and base64url-decode the minted
JWT (stdlib only — no TestClient/httpx). Pins that (1) a kiosk token's VideoGrant is scoped to the
EXACT per-tab room (a token for room X can't join room Y), (2) the kiosk-room guard rejects arbitrary/
non-booth rooms, and (3) the kiosk token carries the ephemeral-room backstop.
"""
import base64
import json

import pytest
from fastapi import HTTPException

from web import server
from web.server import TokenReq


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setattr(server.settings, "livekit_api_key", "devkey")
    monkeypatch.setattr(server.settings, "livekit_api_secret", "secret")


def _claims(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)                 # restore base64url padding
    return json.loads(base64.urlsafe_b64decode(payload))


async def test_kiosk_token_scoped_to_its_unique_room():
    res = await server.token(TokenReq(room="booth-abc123", identity="kiosk", name="K"))
    assert res["room"] == "booth-abc123"
    grant = _claims(res["token"])["video"]
    assert grant["room"] == "booth-abc123" and grant["roomJoin"] is True   # can ONLY join this room


async def test_kiosk_token_carries_ephemeral_room_config():
    res = await server.token(TokenReq(room="booth-xyz", identity="kiosk"))
    rc = _claims(res["token"])["roomConfig"]
    assert rc["emptyTimeout"] == server._ROOM_EMPTY_TIMEOUT
    assert rc["departureTimeout"] == server._ROOM_DEPARTURE_TIMEOUT


async def test_bare_booth_room_is_accepted_for_kiosk():
    res = await server.token(TokenReq(room="booth", identity="kiosk"))   # the physical booth case
    assert _claims(res["token"])["video"]["room"] == "booth"


@pytest.mark.parametrize("bad_room", ["operator-haxx", "../evil", "booth_underscore", "Booth-Caps", ""])
async def test_kiosk_room_guard_rejects_non_booth_rooms(bad_room):
    with pytest.raises(HTTPException) as ei:
        await server.token(TokenReq(room=bad_room, identity="kiosk"))
    assert ei.value.status_code == 400
