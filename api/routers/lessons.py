from uuid import uuid4
from pathlib import Path
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

from tempfile import NamedTemporaryFile
from pdf_to_html import pdf_to_html

router = APIRouter(tags=["lessons"])

async def _blocks_out(l: Lesson) -> list[LessonBlockOut]:
    return [
        LessonBlockOut(position=b.position, type=b.type, text=b.text, image_url=b.image_url, caption=b.caption)
        for b in (l.blocks or [])
    ]

@router.post("/api/lessons/{lesson_id}/pdf-html",
          status_code=200,
          dependencies=[Depends(require_roles("admin","teacher"))])
async def upload_pdf_and_convert_to_html(
    lesson_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    request: Request = None,   # ✅ чтобы построить абсолютный URL
):
    l = await session.get(Lesson, lesson_id)
    if not l:
        raise HTTPException(status_code=404, detail="Lesson not found")

    if file.content_type not in ("application/pdf",):
        raise HTTPException(status_code=415, detail="Only PDF is allowed")

    content = await file.read()
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Not a valid PDF")

    # 1) PDF → HTML (во временный файл)
    with NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(content)
        tmp.flush()
        html = pdf_to_html(Path(tmp.name), scale=96/72, image_mode="auto", clip_oversample=2.0, debug=False)

    # 2) Сохраняем HTML на диск
    htmls_dir = UPLOAD_DIR / "htmls"
    htmls_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid4().hex}.html"
    fpath = htmls_dir / fname
    fpath.write_text(html, encoding="utf-8")

    # 3) Формируем относительный и абсолютный URL
    rel_url = f"/files/htmls/{fname}"
    abs_html_url = abs_url(request, rel_url)

    # 4) По желанию: оставляем копию в БД (как было)
    l.html_content = html
    await session.commit()

    # 5) Возвращаем и факт сохранения, и URL файла
    return {
        "status": "ok",
        "html_saved_to_db": True,
        "file_saved": True,
        "html_url": abs_html_url,
        "length": len(html),
    }

@router.post("/lessons", response_model=LessonOut, status_code=201, dependencies=[Depends(require_roles("admin","teacher"))])
async def create_lesson(payload: LessonCreateIn, session: AsyncSession = Depends(get_session), request: Request = None):
    blocks_payload = [
        {"type": b.type, "text": b.text, "image_url": b.image_url, "caption": b.caption}
        for b in (payload.blocks or [])
    ] if payload.blocks else []

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
        id=lesson.id, subject_id=lesson.subject_id, title=lesson.title,
        status=lesson.status, publish_at=lesson.publish_at,
        pdf_url=lesson.pdf_url, html_content=getattr(lesson, "html_content", None),
        html_url=getattr(lesson, "html_url", None),
    )

@router.patch("/lessons/{lesson_id}", response_model=LessonDetailOut, dependencies=[Depends(require_roles("admin","teacher"))])
async def patch_lesson(lesson_id: int, body: LessonPatchIn, session: AsyncSession = Depends(get_session), request: Request = None):
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
        id=l.id, subject_id=l.subject_id, title=l.title, status=l.status, publish_at=l.publish_at,
        blocks=await _blocks_out(l), group_ids=gids,
        pdf_url=l.pdf_url, html_content=getattr(l, "html_content", None),
        html_url=getattr(l, "html_url", None),
    )

@router.post("/lessons/{lesson_id}/pdf", status_code=201, dependencies=[Depends(require_roles("admin","teacher"))])
async def upload_lesson_pdf(lesson_id: int, file: UploadFile = File(...), session: AsyncSession = Depends(get_session), request: Request = None):
    l = await session.get(Lesson, lesson_id)
    if not l:
        raise HTTPException(status_code=404, detail="Lesson not found")
    if file.content_type not in ("application/pdf",):
        raise HTTPException(status_code=415, detail="Only PDF is allowed")
    content = await file.read()
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Not a valid PDF file")

    folder = UPLOAD_DIR / "pdfs"
    folder.mkdir(parents=True, exist_ok=True)

    fname = f"{uuid4().hex}.pdf"
    fpath = folder / fname
    with open(fpath, "wb") as f:
        f.write(content)

    rel_path = f"/files/pdfs/{fname}"
    l.pdf_url = abs_url(request, rel_path)
    l.pdf_filename = file.filename if hasattr(l, "pdf_filename") else None

    await session.commit()
    return {"status": "ok", "pdf_url": l.pdf_url, "pdf_filename": file.filename}

@router.get("/lessons/accessible/{tg_id}", response_model=list[LessonOut])
async def accessible_lessons(tg_id: int, session: AsyncSession = Depends(get_session)):
    user = await users_repo.get_user_by_tg(session, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    lessons = await lessons_repo.get_accessible_lessons_for_user(session, user_id=user.id)
    return [
        LessonOut(
            id=l.id, subject_id=l.subject_id, title=l.title,
            status=l.status, publish_at=l.publish_at, pdf_url=l.pdf_url
        )
        for l in lessons
    ]

@router.get("/lessons/{lesson_id}", response_model=LessonDetailOut)
async def get_lesson_by_id(
    lesson_id: int,
    session: AsyncSession = Depends(get_session),
    current = Depends(get_current_user),
):
    row = await lessons_repo.get_lesson_detail(session, lesson_id)
    if not row:
        raise HTTPException(status_code=404, detail="Lesson not found")
    l, gids = row
    if not await can_view_lesson(session, current.telegram_id if current else None, gids):
        raise HTTPException(status_code=403, detail="Forbidden")
    return LessonDetailOut(
        id=l.id, subject_id=l.subject_id, title=l.title, status=l.status,
        publish_at=l.publish_at, blocks=await _blocks_out(l), group_ids=gids,
        pdf_url=l.pdf_url, html_content=getattr(l, "html_content", None),
    )

@router.delete("/lessons/{lesson_id}", status_code=204, dependencies=[Depends(require_roles("admin","teacher"))])
async def delete_lesson_api(lesson_id: int, session: AsyncSession = Depends(get_session)):
    ok = await lessons_repo.delete_lesson(session, lesson_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Lesson not found")
    await session.commit()
    return None
