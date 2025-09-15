# tests/test_user_profile.py
import pytest

def H(tg): return {"X-Debug-Tg-Id": str(tg)}

@pytest.mark.anyio
async def test_ensure_user_returns_profile(client):
    tg = 1001
    r = await client.post("/api/user/ensure", json={
        "tg_id": tg, "username": "alice", "first_name": "Alice",
        "last_name": "Doe", "avatar_url": None
    }, headers=H(tg))
    assert r.status_code == 200
