import pytest
from uuid import uuid4
from models import async_session
from repositories import users as users_repo
from repositories import lessons as lessons_repo
from repositories import subjects as subjects_repo  # ← добавили

def H(tg): return {"X-Debug-Tg-Id": str(tg)}

@pytest.mark.anyio
async def test_removing_lesson_groups_revokes_student_access(client):
    student_tg = 5001

    # предмет
    rs = await client.post("/api/subjects", json={"name":"Геометрия"})
    assert rs.status_code == 201
    subject_id = rs.json()["id"]

    # урок (опубликован)
    rl = await client.post("/api/lessons", json={
        "subject_id": subject_id,
        "title":"Треугольники",
        "publish": True,
        "blocks":[{"type":"text","text":"ABC"}]
    }, headers=H(student_tg))
    assert rl.status_code == 201
    lesson_id = rl.json()["id"]

    # уникальное имя группы
    gname = f"G-geo (auto) {uuid4()}"

    # даём доступ через группу и ПРЕДМЕТУ, и УРОКУ
    async with async_session() as session:
        g = await users_repo.create_group(session, name=gname)
        await users_repo.ensure_user(session, student_tg)
        await users_repo.add_user_to_group(session, student_tg, g.id)
        await subjects_repo.set_subject_groups(session, subject_id=subject_id, group_ids=[g.id])   # ← ВАЖНО
        await lessons_repo.set_lesson_groups(session, lesson_id=lesson_id, group_ids=[g.id])      # ← как и раньше
        await session.commit()

    # студент видит урок
    ra = await client.get(f"/api/lessons/accessible/{student_tg}", headers=H(student_tg))
    assert any(x["id"] == lesson_id for x in ra.json())

    # снимаем все группы у урока — доступа быть не должно
    async with async_session() as session:
        await lessons_repo.set_lesson_groups(session, lesson_id=lesson_id, group_ids=[])
        await session.commit()

    rb = await client.get(f"/api/lessons/accessible/{student_tg}", headers=H(student_tg))
    assert all(x["id"] != lesson_id for x in rb.json())
