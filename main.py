from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Optional, Any, List, Tuple, Literal
from datetime import datetime
from uuid import uuid4
from pathlib import Path
import os
from pdf_to_html import pdf_to_html  


from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
try:
    from pydantic import ConfigDict  # pydantic v2
    V2 = True
except Exception:
    V2 = False

from sqlalchemy.ext.asyncio import AsyncSession

# ---- модели/репозитории ----
from models import async_session, init_db, Group, Lesson
from repositories import users as users_repo
from repositories import subjects as subjects_repo
from repositories import lessons as lessons_repo
from tempfile import NamedTemporaryFile

PUBLIC_BACKEND_URL = os.getenv(
    "PUBLIC_BACKEND_URL",
    "https://special-space-yodel-6xgggvwq9vjhr9vq-8000.app.github.dev",
)
# ---------- DB session ----------
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session

# ---------- lifespan ----------
@asynccontextmanager
async def lifespan(app_: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Edu MiniApp API", lifespan=lifespan)

# ---------- CORS ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://telegrammapp-44890.web.app",
        "https://*.app.github.dev",
    ],
    allow_origin_regex=r"https://.*\.app\.github\.dev",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- static ----------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
(UPLOAD_DIR / "pdfs").mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(UPLOAD_DIR), html=False), name="files")

# ---------- helpers ----------
from urllib.parse import urljoin, urlparse

def abs_url(request: Request, maybe_url: Optional[str]) -> Optional[str]:
    """
    Делает абсолютный URL:
    - если maybe_url уже абсолютный — вернуть как есть;
    - иначе строим от PUBLIC_BACKEND_URL (если задан),
      иначе — от origin текущего запроса.
    """
    if not maybe_url:
        return None

    # уже абсолютная?
    try:
        parsed = urlparse(maybe_url)
        if parsed.scheme:
            return maybe_url
    except Exception:
        pass

    base = (PUBLIC_BACKEND_URL or "").strip()
    if base:
        base = base.rstrip("/") + "/"
        return urljoin(base, maybe_url.lstrip("/"))

    # фолбэк на фактический origin (локалка/дев-сервер)
    origin = str(request.base_url).rstrip("/") + "/"
    return urljoin(origin, maybe_url.lstrip("/"))

# ---------- схемы ----------
class EnsureUserIn(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None

class UserProfileOut(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    roles: list[str]
    groups: list[dict]

class SubjectOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    group_ids: list[int]

class SubjectOutFull(SubjectOut):
    pass

class SubjectCreateIn(BaseModel):
    name: str
    description: Optional[str] = None
    group_ids: Optional[list[int]] = None

class SubjectPatchIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    group_ids: Optional[list[int]] = None

class LessonBlockIn(BaseModel):
    type: Literal["text","image"]
    position: Optional[int] = None
    text: Optional[str] = None
    image_url: Optional[str] = None
    caption: Optional[str] = None

class LessonBlockOut(BaseModel):
    position: int
    type: str
    text: Optional[str] = None
    image_url: Optional[str] = None
    caption: Optional[str] = None

class LessonCreateIn(BaseModel):
    subject_id: int
    title: str
    publish: bool = False
    publish_at: Optional[datetime] = None
    group_ids: Optional[list[int]] = None
    user_tg_ids: Optional[list[int]] = None
    pdf_url: Optional[str] = None
    blocks: Optional[list[LessonBlockIn]] = None
    html_content: Optional[str] = None

class LessonPatchIn(BaseModel):
    title: Optional[str] = None
    publish: Optional[bool] = None
    publish_at: Optional[datetime] = None
    group_ids: Optional[list[int]] = None
    user_tg_ids: Optional[list[int]] = None
    pdf_url: Optional[str] = None
    blocks: Optional[list[LessonBlockIn]] = None
    html_content: Optional[str] = None

class LessonOut(BaseModel):
    id: int
    title: str
    status: Optional[str] = None
    publish_at: Optional[datetime] = None
    subject_id: Optional[int] = None
    subject_name: Optional[str] = None
    group_ids: List[int] = []
    pdf_url: Optional[str] = None
    pdf_filename: Optional[str] = None
    blocks: Optional[List[Any]] = None
    if V2:
        model_config = ConfigDict(from_attributes=True)
    else:
        class Config: orm_mode = True
    html_content: Optional[str] = None

class LessonDetailOut(LessonOut):
    blocks: list[LessonBlockOut] = []
    group_ids: list[int] = []
    html_content: Optional[str] = None

class LessonListItemOut(BaseModel):
    id: int
    subject_id: int
    title: str
    status: str
    publish_at: Optional[datetime] = None
    group_ids: list[int]

class RoleMemberIn(BaseModel):
    tg_id: int

class GroupMemberIn(BaseModel):
    tg_id: int

class GroupCreateIn(BaseModel):
    name: str

class GroupPatchIn(BaseModel):
    name: str

# ---------- auth ----------
async def get_current_user(
    session: AsyncSession = Depends(get_session),
    x_debug_tg_id: Optional[int] = Header(None, alias="X-Debug-Tg-Id"),
):
    if not x_debug_tg_id:
        raise HTTPException(status_code=401, detail="Missing X-Debug-Tg-Id")
    user = await users_repo.get_user_by_tg(session, x_debug_tg_id)
    if not user:
        user = await users_repo.ensure_user(session, x_debug_tg_id)
        await session.commit()
    return user

def require_roles(*allowed: str):
    async def checker(
        me = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ):
        prof = await users_repo.get_user_profile(session, me.telegram_id)
        codes = {r.code for r in (prof.roles if prof else [])}
        if not (set(allowed) & codes):
            raise HTTPException(status_code=403, detail="Forbidden")
        return prof or me
    return checker

async def _can_view_lesson(session: AsyncSession, tg_id: Optional[int], lesson_group_ids: list[int]) -> bool:
    if not lesson_group_ids:
        return True
    if not tg_id:
        return False
    prof = await users_repo.get_user_profile(session, tg_id)
    if not prof:
        return False
    codes = {r.code for r in prof.roles}
    if "admin" in codes or "teacher" in codes:
        return True
    my_groups = {g.id for g in prof.groups}
    return any(g in my_groups for g in lesson_group_ids)

# ---------- basic ----------
@app.get("/api/health")
async def health():
    return {"status": "ok"}

# ---------- users ----------
@app.post("/api/user/ensure", response_model=UserProfileOut)
async def api_ensure_user(data: EnsureUserIn, session: AsyncSession = Depends(get_session)):
    await users_repo.ensure_user(
        session, data.tg_id,
        username=data.username, first_name=data.first_name,
        last_name=data.last_name, avatar_url=data.avatar_url,
    )
    await session.commit()
    prof = await users_repo.get_user_profile(session, data.tg_id)
    return UserProfileOut(
        tg_id=prof.telegram_id, username=prof.username, first_name=prof.first_name,
        last_name=prof.last_name, avatar_url=prof.avatar_url,
        roles=[r.code for r in prof.roles],
        groups=[{"id": g.id, "name": g.name} for g in prof.groups],
    )

@app.get("/api/user/profile/{tg_id}", response_model=UserProfileOut)
async def get_user_profile(tg_id: int, session: AsyncSession = Depends(get_session)):
    user = await users_repo.get_user_profile(session, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfileOut(
        tg_id=user.telegram_id, username=user.username, first_name=user.first_name,
        last_name=user.last_name, avatar_url=user.avatar_url,
        roles=[r.code for r in user.roles],
        groups=[{"id": g.id, "name": g.name} for g in user.groups],
    )

@app.get("/api/users", dependencies=[Depends(require_roles("admin","teacher"))])
async def list_users(session: AsyncSession = Depends(get_session)):
    users = await users_repo.list_users_with_details(session)
    return [
        {
            "tg_id": u.telegram_id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "avatar_url": u.avatar_url,
            "roles": [r.code for r in u.roles],
            "groups": [{"id": g.id, "name": g.name} for g in u.groups],
        } for u in users
    ]

# ---------- subjects ----------
@app.get("/api/subjects", response_model=list[SubjectOut])
async def list_subjects(session: AsyncSession = Depends(get_session)):
    rows = await subjects_repo.list_subjects_with_group_ids(session)
    return [SubjectOut(id=s.id, name=s.name, description=s.description, group_ids=gids) for (s, gids) in rows]

@app.get("/api/subjects/{subject_id}", response_model=SubjectOutFull)
async def get_subject(subject_id: int, session: AsyncSession = Depends(get_session)):
    row = await subjects_repo.get_subject_with_group_ids(session, subject_id)
    if not row:
        raise HTTPException(status_code=404, detail="Subject not found")
    s, gids = row
    return SubjectOutFull(id=s.id, name=s.name, description=s.description, group_ids=gids)

@app.post("/api/subjects", response_model=SubjectOut, status_code=201, dependencies=[Depends(require_roles("admin","teacher"))])
async def create_subject(payload: SubjectCreateIn, session: AsyncSession = Depends(get_session)):
    subj = await subjects_repo.create_subject(session, name=payload.name, description=payload.description)
    await session.flush()
    if payload.group_ids is not None:
        await subjects_repo.set_subject_groups(session, subject_id=subj.id, group_ids=payload.group_ids)
    await session.commit()
    s, gids = await subjects_repo.get_subject_with_group_ids(session, subj.id)
    return SubjectOut(id=s.id, name=s.name, description=s.description, group_ids=gids)

@app.patch("/api/subjects/{subject_id}", response_model=SubjectOutFull, dependencies=[Depends(require_roles("admin","teacher"))])
async def patch_subject(subject_id: int, body: SubjectPatchIn, session: AsyncSession = Depends(get_session)):
    subj = await subjects_repo.update_subject(session, subject_id=subject_id, name=body.name, description=body.description, group_ids=body.group_ids)
    if not subj:
        raise HTTPException(status_code=404, detail="Subject not found")
    await session.commit()
    s, gids = await subjects_repo.get_subject_with_group_ids(session, subject_id)
    return SubjectOutFull(id=s.id, name=s.name, description=s.description, group_ids=gids)

@app.get("/api/subjects/{subject_id}/lessons", response_model=list[LessonListItemOut], dependencies=[Depends(require_roles("admin","teacher"))])
async def subject_lessons(subject_id: int, session: AsyncSession = Depends(get_session)):
    rows = await lessons_repo.list_subject_lessons_with_group_ids(session, subject_id)
    return [
        LessonListItemOut(id=l.id, subject_id=l.subject_id, title=l.title, status=l.status, publish_at=l.publish_at, group_ids=gids)
        for (l, gids) in rows
    ]

# ---------- lessons ----------
@app.post("/api/lessons/{lesson_id}/pdf-html",
          status_code=200,
          dependencies=[Depends(require_roles("admin","teacher"))])
async def upload_pdf_and_convert_to_html(
    lesson_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    # 1) Валидируем урок
    l = await session.get(Lesson, lesson_id)
    if not l:
        raise HTTPException(status_code=404, detail="Lesson not found")

    # 2) Проверяем тип
    if file.content_type not in ("application/pdf",):
        raise HTTPException(status_code=415, detail="Only PDF is allowed")

    content = await file.read()
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Not a valid PDF")

    # 3) Конвертируем во временный файл → HTML (через твою функцию)
    with NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(content)
        tmp.flush()
        html = pdf_to_html(Path(tmp.name), scale=96/72, image_mode="auto", clip_oversample=2.0, debug=False)

    # 4) Сохраняем в урок
    l.html_content = html
    await session.commit()

    # 5) Возвращаем html (и флаг)
    return {"status": "ok", "html_saved": True, "length": len(html)}

async def _blocks_out(l: Lesson) -> list[LessonBlockOut]:
    return [
        LessonBlockOut(position=b.position, type=b.type, text=b.text, image_url=b.image_url, caption=b.caption)
        for b in (l.blocks or [])
    ]

@app.post("/api/lessons", response_model=LessonOut, status_code=201, dependencies=[Depends(require_roles("admin","teacher"))])
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

    await session.commit()

    return LessonOut(
        id=lesson.id, subject_id=lesson.subject_id, title=lesson.title,
        status=lesson.status, publish_at=lesson.publish_at,
        pdf_url=lesson.pdf_url, html_content=getattr(lesson, "html_content", None),  # NEW
    )


@app.patch("/api/lessons/{lesson_id}", response_model=LessonDetailOut, dependencies=[Depends(require_roles("admin","teacher"))])
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

    await session.commit()

    l, gids = await lessons_repo.get_lesson_detail(session, lesson_id)
    return LessonDetailOut(
        id=l.id, subject_id=l.subject_id, title=l.title, status=l.status, publish_at=l.publish_at,
        blocks=await _blocks_out(l), group_ids=gids,
        pdf_url=l.pdf_url, html_content=getattr(l, "html_content", None),  # NEW
    )


@app.post("/api/lessons/{lesson_id}/pdf", status_code=201, dependencies=[Depends(require_roles("admin","teacher"))])
async def upload_lesson_pdf(
    lesson_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    request: Request = None,
):
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

    # формируем ссылку через abs_url → отдаст PUBLIC_BACKEND_URL при его наличии
    rel_path = f"/files/pdfs/{fname}"
    l.pdf_url = abs_url(request, rel_path)
    l.pdf_filename = file.filename if hasattr(l, "pdf_filename") else None

    await session.commit()
    return {"status": "ok", "pdf_url": l.pdf_url, "pdf_filename": file.filename}

@app.get("/api/lessons/accessible/{tg_id}", response_model=list[LessonOut])
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

@app.get("/api/lessons/{lesson_id}", response_model=LessonDetailOut)
async def get_lesson_by_id(
        lesson_id: int,
        session: AsyncSession = Depends(get_session),
        x_debug_tg_id: Optional[int] = Header(None, alias="X-Debug-Tg-Id"),
    ):
    row: Optional[Tuple[Lesson, List[int]]] = await lessons_repo.get_lesson_detail(session, lesson_id)
    if not row:
        raise HTTPException(status_code=404, detail="Lesson not found")
    l, gids = row
    if not await _can_view_lesson(session, x_debug_tg_id, gids):
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

# ---------- roles ----------
@app.get("/api/roles", dependencies=[Depends(require_roles("admin","teacher"))])
async def get_roles(session: AsyncSession = Depends(get_session)):
    rows = await users_repo.list_roles_with_members(session)
    return [
        {"key": role.code, "title": role.name,
         "members": [{"tg_id": u.telegram_id, "first_name": u.first_name, "last_name": u.last_name,
                      "username": u.username, "avatar_url": u.avatar_url} for u in members]}
        for role, members in rows
    ]

@app.post("/api/roles/{role}/members", dependencies=[Depends(require_roles("admin","teacher"))])
async def add_role_member(role: str, body: RoleMemberIn, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.add_role_to_user(session, body.tg_id, role)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    await session.commit()
    return {"status": "ok"}

@app.delete("/api/roles/{role}/members/{tg_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def remove_role_member(role: str, tg_id: int, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.remove_role_from_user(session, tg_id, role)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    await session.commit()
    return {"status": "ok"}

# ---------- groups ----------
@app.get("/api/groups", dependencies=[Depends(require_roles("admin","teacher"))])
async def list_groups(session: AsyncSession = Depends(get_session)):
    groups = await users_repo.list_groups_with_members(session)
    return [{"id": g.id, "name": g.name,
             "members": [{"tg_id": u.telegram_id, "first_name": u.first_name, "last_name": u.last_name,
                          "username": u.username, "avatar_url": u.avatar_url} for u in g.members]}
            for g in groups]

@app.post("/api/groups", dependencies=[Depends(require_roles("admin","teacher"))], status_code=201)
async def create_group(body: GroupCreateIn, session: AsyncSession = Depends(get_session)):
    g = await users_repo.create_group(session, name=body.name)
    await session.commit()
    return {"id": g.id, "name": g.name}

@app.patch("/api/groups/{group_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def patch_group(group_id: int, body: GroupPatchIn, session: AsyncSession = Depends(get_session)):
    g = await session.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    g.name = body.name
    await session.flush()
    await session.commit()
    return {"id": g.id, "name": g.name}

@app.post("/api/groups/{group_id}/members", dependencies=[Depends(require_roles("admin","teacher"))])
async def add_group_member(group_id: int, body: GroupMemberIn, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.add_user_to_group(session, body.tg_id, group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User or group not found")
    await session.commit()
    return {"status": "ok"}

@app.delete("/api/groups/{group_id}/members/{tg_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def remove_group_member(group_id: int, tg_id: int, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.remove_user_from_group(session, tg_id, group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User or group not found")
    await session.commit()
    return {"status": "ok"}

@app.delete("/api/groups/{group_id}", dependencies=[Depends(require_roles("admin","teacher"))], status_code=204)
async def delete_group(group_id: int, session: AsyncSession = Depends(get_session)):
    g = await session.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    await session.delete(g)
    await session.commit()
    return None
