import pytest

def H(tg): return {"X-Debug-Tg-Id": str(tg)}

@pytest.mark.anyio
async def test_published_lesson_without_groups_is_invisible_for_student(client):
    student_tg = 2001

    # пользователь должен существовать
    r_ens = await client.post("/api/user/ensure", json={"tg_id": student_tg})
    assert r_ens.status_code == 200

    # создаём предмет
    rs = await client.post("/api/subjects", json={"name":"Математика","description":"Алгебра"})
    assert rs.status_code == 201
    subject_id = rs.json()["id"]

    # урок опубликован, но групп нет
    rl = await client.post("/api/lessons", json={
        "subject_id": subject_id,
        "title": "Урок 1",
        "publish": True,
        "blocks": [{"type":"text","text":"Привет!"}]
    }, headers=H(student_tg))
    assert rl.status_code == 201
    lesson_id = rl.json()["id"]

    # студент не должен видеть урок
    ra = await client.get(f"/api/lessons/accessible/{student_tg}", headers=H(student_tg))
    assert ra.status_code == 200
    ids = [x["id"] for x in ra.json()]
    assert lesson_id not in ids
