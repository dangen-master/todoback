import pytest
from uuid import uuid4
from models import async_session
from repositories import users as users_repo
from repositories import lessons as lessons_repo
from repositories import subjects as subjects_repo  # ← добавили

def H(tg): return {"X-Debug-Tg-Id": str(tg)}

@pytest.mark.anyio
async def test_lesson_visible_when_user_group_intersects_lesson_groups(client):
    student_tg = 3001

    # предмет и опубликованный урок
    rs = await client.post("/api/subjects", json={"name":"Физика"})
    assert rs.status_code == 201
    subject_id = rs.json()["id"]

    rl = await client.post("/api/lessons", json={
        "subject_id": subject_id,
        "title": "Кинематика",
        "publish": True,
        "blocks": [{"type":"text","text":"Материал"}]
    }, headers=H(student_tg))
    assert rl.status_code == 201
    lesson_id = rl.json()["id"]

    gname = f"Группа А (auto) {uuid4()}"

    # создаём группу, включаем студента и привязываем группу и к предмету, и к уроку
    async with async_session() as session:
        g = await users_repo.create_group(session, name=gname)
        await users_repo.ensure_user(session, student_tg)
        await users_repo.add_user_to_group(session, student_tg, g.id)
        await subjects_repo.set_subject_groups(session, subject_id=subject_id, group_ids=[g.id])  # ← ВАЖНО
        await lessons_repo.set_lesson_groups(session, lesson_id=lesson_id, group_ids=[g.id])      # ← как и раньше
        await session.commit()

    # студент должен видеть урок
    ra = await client.get(f"/api/lessons/accessible/{student_tg}", headers=H(student_tg))
    assert ra.status_code == 200
    ids = [x["id"] for x in ra.json()]
    assert lesson_id in ids
