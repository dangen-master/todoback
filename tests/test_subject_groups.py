# tests/test_subject_groups.py
import pytest
from uuid import uuid4
from httpx import ASGITransport, AsyncClient
from main import app
from models import async_session
from repositories import users as users_repo

@pytest.mark.anyio
async def test_subject_group_ids_roundtrip(client):
    name1 = f"G1_{uuid4().hex[:6]}"
    name2 = f"G2_{uuid4().hex[:6]}"

    async with async_session() as session:
        g1 = await users_repo.create_group(session, name=name1)
        g2 = await users_repo.create_group(session, name=name2)
        await session.commit()
        g1_id, g2_id = g1.id, g2.id

    r = await client.post("/api/subjects", json={
        "name": "Информатика",
        "description": "Базовые понятия",
        "group_ids": [g1_id, g2_id]
    })
    assert r.status_code == 201
    s = r.json()
    assert sorted(s["group_ids"]) == sorted([g1_id, g2_id])

    rd = await client.get(f"/api/subjects/{s['id']}")
    assert rd.status_code == 200
    assert sorted(rd.json()["group_ids"]) == sorted([g1_id, g2_id])
