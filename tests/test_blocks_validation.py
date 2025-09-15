import pytest

@pytest.mark.anyio
async def test_block_validation_fails_on_missing_text_for_text_type(client):
    # создаём предмет
    rs = await client.post("/api/subjects", json={"name": "Русский язык"})
    assert rs.status_code == 201
    subject_id = rs.json()["id"]

    # некорректный блок: type="text", но нет поля text → ожидаем 400
    r = await client.post("/api/lessons", json={
        "subject_id": subject_id,
        "title": "Орфография",
        "publish": True,
        "blocks": [{"type": "text"}]
    })
    assert r.status_code == 400
