# app/api/routers/lessons.py
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import asyncio

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, require_roles, get_current_user, can_view_lesson
from api.schemas import (
    LessonCreateIn, LessonPatchIn,
    LessonOut, LessonDetailOut, LessonBlockOut
)
from api.utils import UPLOAD_DIR, abs_url, save_html_to_uploads
from models import Lesson
from repositories import users as users_repo
from repositories import lessons as lessons_repo

router = APIRouter(tags=["lessons"])

# =========================================================
# logging (отдельная папка uploads/logs)
# =========================================================

LOG_DIR = UPLOAD_DIR / "logs"
LOG_FILE = LOG_DIR / "pdf_html.log"

def _write_log(line: str) -> None:
    """Пишем строку в uploads/logs/pdf_html.log (UTF-8, с таймстампом). Никогда не падаем из-за логгера."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ts} {line}\n")
    except Exception:
        # не мешаем основному потоку работы
        pass

# =========================================================
# helpers
# =========================================================

async def _blocks_out(l: Lesson) -> list[LessonBlockOut]:
    return [
        LessonBlockOut(position=b.position, type=b.type, text=b.text, image_url=b.image_url, caption=b.caption)
        for b in (l.blocks or [])
    ]

# ——— Санитайзеры имён с сохранением Unicode/кириллицы ———
_WIN_FORBIDDEN = r'\\/:*?"<>|'
_CTRL_CHARS = r"[\u0000-\u001F\u007F]"

def sanitize_filename(name: str | None, *, fallback: str = "document.pdf") -> str:
    """
    Для PDF: сохраняем Unicode/кириллицу, убираем только опасные символы путей, схлопываем пробелы.
    Гарантируем расширение .pdf. Длина — до ~180 символов.
    """
    base = (name or fallback).strip()
    base = re.sub(_CTRL_CHARS, "", base)
    base = re.sub(f"[{re.escape(_WIN_FORBIDDEN)}]+", "", base)
    base = re.sub(r"\s+", " ", base).strip()
    if not base:
        base = fallback
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base[:180]

def sanitize_html_name(name: str | None, *, fallback: str = "document.html") -> str:
    """Для HTML: те же правила, расширение .html."""
    pdf_like = sanitize_filename(name or "document.pdf")
    html_name = Path(pdf_like).with_suffix(".html").name
    return html_name[:180]

def unique_named_path(folder: Path, filename: str) -> Path:
    """
    Возвращает уникальный путь для имени/расширения:
      name.ext, name-1.ext, name-2.ext, ...
    """
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / filename
    if not p.exists():
        return p
    stem = Path(filename).stem
    ext = Path(filename).suffix
    i = 1
    while True:
        cand = folder / f"{stem}-{i}{ext}"
        if not cand.exists():
            return cand
        i += 1

# =========================================================
# routes
# =========================================================

@router.post(
    "/lessons/{lesson_id}/pdf-html",
    status_code=200,
    dependencies=[Depends(require_roles("admin", "teacher"))],
)
async def upload_pdf_and_convert_to_html(
    lesson_id: int,
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """
    Загружаем PDF с «родным» именем (Unicode ок), конвертируем его в HTML.
    HTML получает такое же «родное» имя, но с .html.
    Конвертер вызывается строго как pdf_to_html(pdf_path, html_path).
    Перед вызовом и при ошибках пишем информацию в uploads/logs/pdf_html.log.
    """
    l = await session.get(Lesson, lesson_id)
    if not l:
        raise HTTPException(status_code=404, detail="Lesson not found")

    if file.content_type not in ("application/pdf",):
        raise HTTPException(status_code=415, detail="Only PDF is allowed")

    content = await file.read()
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Not a valid PDF")

    pdfs_dir = UPLOAD_DIR / "pdfs"
    htmls_dir = UPLOAD_DIR / "htmls"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    htmls_dir.mkdir(parents=True, exist_ok=True)

    # 1) Сохраняем PDF на диск с нормальным именем (с кириллицей)
    original_pdf_name = sanitize_filename(file.filename or getattr(l, "pdf_filename", None) or "document.pdf")
    pdf_path = unique_named_path(pdfs_dir, original_pdf_name)
    pdf_path.write_bytes(content)

    # Проставляем ссылку и «родное» имя в БД
    rel_pdf = f"/files/pdfs/{pdf_path.name}"
    l.pdf_url = abs_url(request, rel_pdf)
    if hasattr(l, "pdf_filename"):
        l.pdf_filename = original_pdf_name
    await session.commit()

    # Небольшая пауза для файловой системы (редко, но помогает)
    await asyncio.sleep(0.05)

    # 2) Конвертация PDF -> HTML (строго pdf_to_html(pdf_path, html_path))
    preferred_name = getattr(l, "pdf_filename", None) or (file.filename or pdf_path.name)
    html_filename = sanitize_html_name(preferred_name, fallback="document.html")
    html_path = unique_named_path(htmls_dir, html_filename)

    # ЛОГ: записываем пути, вместо print
    _write_log(f"pdf_to_html call: pdf_path={pdf_path} html_path={html_path}")

    try:
        from pdf_to_html import pdf_to_html  # функция должна записать HTML в html_path
    except Exception as e:
        _write_log(f"[import_error] pdf_to_html import failed: {e!r} (pdf={pdf_path}, html={html_path})")
        raise HTTPException(status_code=500, detail=f"pdf_to_html import failed: {e!r}")

    try:
        pdf_to_html(pdf_path, html_path)  # ← строго так, без CLI и доп. аргументов
    except Exception as e:
        _write_log(f"[convert_error] PDF->HTML failed: {e!r} (pdf={pdf_path}, html={html_path})")
        raise HTTPException(status_code=500, detail=f"PDF->HTML failed: {e!r}")

    if not html_path.exists() or html_path.stat().st_size < 32:
        _write_log(f"[convert_error] HTML empty or missing (pdf={pdf_path}, html={html_path})")
        raise HTTPException(status_code=500, detail="PDF->HTML produced empty output")

    # читаем HTML, кладём в БД и формируем URL
    html_text = html_path.read_text(encoding="utf-8")
    rel_html = f"/files/htmls/{html_path.name}"

    l.html_content = html_text
    if hasattr(l, "html_url"):
        l.html_url = abs_url(request, rel_html)
    await session.commit()

    return {
        "status": "ok",
        "pdf_file": pdf_path.name,
        "html_file": html_path.name,
        "pdf_url": abs_url(request, rel_pdf),
        "html_url": abs_url(request, rel_html),
        "html_bytes": len(html_text.encode("utf-8")),
    }


@router.post(
    "/lessons",
    response_model=LessonOut,
    status_code=201,
    dependencies=[Depends(require_roles("admin", "teacher"))],
)
async def create_lesson(
    request: Request,
    payload: LessonCreateIn,
    session: AsyncSession = Depends(get_session),
):
    blocks_payload = (
        [
            {"type": b.type, "text": b.text, "image_url": b.image_url, "caption": b.caption}
            for b in (payload.blocks or [])
        ]
        if payload.blocks
        else []
    )

    try:
        lesson = await lessons_repo.create_lesson(
            session,
            subject_id=payload.subject_id,
            title=payload.title,
            blocks=blocks_payload if not payload.pdf_url else [],
            publish=payload.publish,
            publish_at=payload.publish_at,
        )
    except lessons_repo.SubjectNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except lessons_repo.PayloadInvalidError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if payload.pdf_url:
        db_l = await session.get(Lesson, lesson.id)
        db_l.pdf_url = abs_url(request, payload.pdf_url)
        lesson.pdf_url = db_l.pdf_url

    if payload.group_ids is not None:
        await lessons_repo.set_lesson_groups(session, lesson_id=lesson.id, group_ids=payload.group_ids)

    if payload.user_tg_ids:
        ids: list[int] = []
        for tg in payload.user_tg_ids:
            u = await users_repo.ensure_user(session, tg)
            ids.append(u.id)
        await lessons_repo.update_lesson(session, lesson_id=lesson.id, user_ids=ids)

    if payload.html_content is not None:
        db_l = await session.get(Lesson, lesson.id)
        db_l.html_content = payload.html_content
        db_l.html_url = save_html_to_uploads(payload.html_content, request)

    await session.commit()

    return LessonOut(
        id=lesson.id,
        subject_id=lesson.subject_id,
        title=lesson.title,
        status=lesson.status,
        publish_at=lesson.publish_at,
        pdf_url=lesson.pdf_url,
        html_content=getattr(lesson, "html_content", None),
        html_url=getattr(lesson, "html_url", None),
    )


@router.patch(
    "/lessons/{lesson_id}",
    response_model=LessonDetailOut,
    dependencies=[Depends(require_roles("admin", "teacher"))],
)
async def patch_lesson(
    lesson_id: int,
    request: Request,
    body: LessonPatchIn,
    session: AsyncSession = Depends(get_session),
):
    blocks_payload = None
    if body.blocks is not None:
        blocks_payload = [
            {"type": b.type, "text": b.text, "image_url": b.image_url, "caption": b.caption}
            for b in body.blocks
        ]

    user_ids = None
    if body.user_tg_ids:
        user_ids = []
        for tg in body.user_tg_ids:
            u = await users_repo.ensure_user(session, tg)
            user_ids.append(u.id)

    l = await lessons_repo.update_lesson(
        session,
        lesson_id=lesson_id,
        title=body.title,
        publish=body.publish,
        publish_at=body.publish_at,
        blocks=blocks_payload,
        group_ids=body.group_ids,
        user_ids=user_ids,
    )
    if not l:
        raise HTTPException(status_code=404, detail="Lesson not found")

    if body.pdf_url is not None:
        db_l = await session.get(Lesson, lesson_id)
        db_l.pdf_url = abs_url(request, body.pdf_url)

    if body.html_content is not None:
        db_l = await session.get(Lesson, lesson_id)
        db_l.html_content = body.html_content
        db_l.html_url = save_html_to_uploads(body.html_content, request)

    await session.commit()

    l, gids = await lessons_repo.get_lesson_detail(session, lesson_id)
    return LessonDetailOut(
        id=l.id,
        subject_id=l.subject_id,
        title=l.title,
        status=l.status,
        publish_at=l.publish_at,
        blocks=await _blocks_out(l),
        group_ids=gids,
        pdf_url=l.pdf_url,
        html_content=getattr(l, "html_content", None),
        html_url=getattr(l, "html_url", None),
    )


@router.post(
    "/lessons/{lesson_id}/pdf",
    status_code=201,
    dependencies=[Depends(require_roles("admin", "teacher"))],
)
async def upload_lesson_pdf(
    lesson_id: int,
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """
    Простая загрузка PDF (без конвертации). Имя берётся «как есть» (после мягкого sanitize),
    сохраняется человекочитаемым, с Unicode/кириллицей, с уникализацией -1, -2, ...
    """
    l = await session.get(Lesson, lesson_id)
    if not l:
        raise HTTPException(status_code=404, detail="Lesson not found")
    if file.content_type not in ("application/pdf",):
        raise HTTPException(status_code=415, detail="Only PDF is allowed")

    content = await file.read()
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Not a valid PDF file")

    folder = UPLOAD_DIR / "pdfs"
    original_name = sanitize_filename(file.filename or getattr(l, "pdf_filename", None) or "document.pdf")
    fpath = unique_named_path(folder, original_name)
    fpath.write_bytes(content)

    rel_path = f"/files/pdfs/{fpath.name}"
    l.pdf_url = abs_url(request, rel_path)
    if hasattr(l, "pdf_filename"):
        l.pdf_filename = original_name

    await session.commit()
    return {"status": "ok", "pdf_url": l.pdf_url, "pdf_filename": original_name}


@router.get("/lessons/accessible/{tg_id}", response_model=list[LessonOut])
async def accessible_lessons(tg_id: int, session: AsyncSession = Depends(get_session)):
    user = await users_repo.get_user_by_tg(session, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    lessons = await lessons_repo.get_accessible_lessons_for_user(session, user_id=user.id)
    return [
        LessonOut(
            id=l.id,
            subject_id=l.subject_id,
            title=l.title,
            status=l.status,
            publish_at=l.publish_at,
            pdf_url=l.pdf_url,
        )
        for l in lessons
    ]


@router.get("/lessons/{lesson_id}", response_model=LessonDetailOut)
async def get_lesson_by_id(
    lesson_id: int,
    session: AsyncSession = Depends(get_session),
    current=Depends(get_current_user),
):
    row = await lessons_repo.get_lesson_detail(session, lesson_id)
    if not row:
        raise HTTPException(status_code=404, detail="Lesson not found")
    l, gids = row
    if not await can_view_lesson(session, current.telegram_id if current else None, gids):
        raise HTTPException(status_code=403, detail="Forbidden")
    return LessonDetailOut(
        id=l.id,
        subject_id=l.subject_id,
        title=l.title,
        status=l.status,
        publish_at=l.publish_at,
        blocks=await _blocks_out(l),
        group_ids=gids,
        pdf_url=l.pdf_url,
        html_content=getattr(l, "html_content", None),
    )


@router.delete("/lessons/{lesson_id}", status_code=204, dependencies=[Depends(require_roles("admin", "teacher"))])
async def delete_lesson_api(lesson_id: int, session: AsyncSession = Depends(get_session)):
    ok = await lessons_repo.delete_lesson(session, lesson_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Lesson not found")
    await session.commit()
    return None
