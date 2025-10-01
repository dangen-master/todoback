from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Optional, Callable, Awaitable, Any, Literal, List
from datetime import datetime
from uuid import uuid4
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, status, Header, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator,  AliasChoices, ConfigDict, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from models import async_session, init_db, Group  # ← берем Group из models
from repositories import users as users_repo
from repositories import subjects as subjects_repo
from repositories import lessons as lessons_repo
from models import Lesson


# ---------- DB session ----------
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session

# ---------- Auth / RBAC ----------
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

def require_roles(*allowed: str) -> Callable[..., Awaitable[Any]]:
    async def checker(
        me = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ):
        prof = await users_repo.get_user_profile(session, me.telegram_id)
        codes = {r.code for r in prof.roles} if prof else set()
        if not (set(allowed) & codes):
            raise HTTPException(status_code=403, detail="Forbidden")
        return prof or me
    return checker

# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app_: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Edu MiniApp API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://web.telegram.org",
        "https://telegram.org",
        "https://*.telegram.org",
        "https://telegrammapp-44890.web.app"
    ],
    allow_origin_regex=r"^https:\/\/.*\.telegram\.org$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(UPLOAD_DIR)), name="files")
# ---------- Schemas ----------
class EnsureUserIn(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None

class SubjectCreateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: Optional[str] = None
    # примем и group_ids, и groupIds
    group_ids: Optional[list[int]] = Field(
        default=None, validation_alias=AliasChoices("group_ids", "groupIds")
    )

class LessonBlockIn(BaseModel):
    type: Literal['text', 'image']
    position: Optional[int] = None
    text: Optional[str] = None
    image_url: Optional[str] = None
    caption: Optional[str] = None

    # Жёсткая проверка для НЕпустых блоков остаётся
    @model_validator(mode='after')
    def _require_payload_for_type(self):
        if self.type == 'text' and not (self.text and self.text.strip()):
            raise ValueError("text is required when type='text'")
        if self.type == 'image' and not (self.image_url):
            raise ValueError("image_url is required when type='image'")
        return self

class LessonCreateIn(BaseModel):
    subject_id: int
    title: str
    publish: bool = False

    # NEW: либо блоки, либо pdf_url
    pdf_url: Optional[str] = None

    # было: blocks: List[dict] = Field(default_factory=list)
    blocks: List[dict] = Field(default_factory=list)

    # NEW: чтобы не падать при обращении к этим полям в create_lesson
    publish_at: Optional[datetime] = Field(default=None, validation_alias=AliasChoices("publish_at", "publishAt"))
    group_ids: Optional[list[int]] = Field(default=None, validation_alias=AliasChoices("group_ids", "groupIds"))
    user_tg_ids: Optional[list[int]] = Field(default=None, validation_alias=AliasChoices("user_tg_ids", "userTgIds"))

    @field_validator("blocks", mode="before")
    @classmethod
    def _drop_empty_blocks(cls, v):
        items = v or []
        cleaned = []
        for b in items:
            t = (b or {}).get("type")
            if t == "text":
                txt = (b or {}).get("text") or ""
                if txt.strip():
                    cleaned.append(b)
            elif t == "image":
                url = (b or {}).get("image_url")
                if url:
                    cleaned.append(b)
        return cleaned

    @model_validator(mode="after")
    def _cast_blocks(self):
        # если пришёл pdf_url — блоки очищаем (в таком режиме контентом служит PDF)
        if self.pdf_url:
            self.blocks = []
            return self
        self.blocks = [LessonBlockIn(**b) for b in self.blocks]
        return self


class GrantUsersIn(BaseModel):
    lesson_id: int
    user_tg_ids: list[int]

class GrantGroupsIn(BaseModel):
    lesson_id: int
    group_ids: list[int]

class UserProfileOut(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    roles: list[str]
    groups: list[dict]

class RoleMemberIn(BaseModel):
    tg_id: int

class GroupMemberIn(BaseModel):
    tg_id: int

class GroupCreateIn(BaseModel):
    name: str

class GroupPatchIn(BaseModel):
    name: str

class SubjectOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    group_ids: list[int]

class SubjectOutFull(SubjectOut):
    pass

class SubjectPatchIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    description: Optional[str] = None
    group_ids: Optional[list[int]] = Field(
        default=None, validation_alias=AliasChoices("group_ids", "groupIds")
    )

class LessonBlockOut(BaseModel):
    position: int
    type: str
    text: Optional[str] = None
    image_url: Optional[str] = None
    caption: Optional[str] = None

class LessonOut(BaseModel):
    id: int
    subject_id: int
    title: str
    status: str
    publish_at: datetime | None = None
    pdf_url: Optional[str] = None

class LessonDetailOut(LessonOut):
    blocks: list[LessonBlockOut]
    group_ids: list[int]

class LessonPatchIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Optional[str] = None
    publish: Optional[bool] = None
    publish_at: Optional[datetime] = Field(
        default=None, validation_alias=AliasChoices("publish_at", "publishAt")
    )
    blocks: Optional[list[LessonBlockIn]] = None
    group_ids: Optional[list[int]] = Field(
        default=None, validation_alias=AliasChoices("group_ids", "groupIds")
    )
    user_tg_ids: Optional[list[int]] = Field(
        default=None, validation_alias=AliasChoices("user_tg_ids", "userTgIds")
    )
    pdf_url: Optional[str] = None

class LessonListItemOut(BaseModel):
    id: int
    subject_id: int
    title: str
    status: str
    publish_at: Optional[datetime] = None
    group_ids: list[int]

# ---------- Endpoints ----------
@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "Сервер работает"}

@app.get("/api/users", dependencies=[Depends(require_roles("admin", "teacher"))])
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
        tg_id=prof.telegram_id, username=prof.username,
        first_name=prof.first_name, last_name=prof.last_name,
        avatar_url=prof.avatar_url, roles=[r.code for r in prof.roles],
        groups=[{"id": g.id, "name": g.name} for g in prof.groups],
    )

@app.get("/api/user/profile/{tg_id}", response_model=UserProfileOut)
async def get_user_profile(tg_id: int, session: AsyncSession = Depends(get_session)):
    user = await users_repo.get_user_profile(session, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfileOut(
        tg_id=user.telegram_id, username=user.username,
        first_name=user.first_name, last_name=user.last_name,
        avatar_url=user.avatar_url, roles=[r.code for r in user.roles],
        groups=[{"id": g.id, "name": g.name} for g in user.groups],
    )

# -------- Subjects --------
@app.get("/api/subjects", response_model=list[SubjectOut])
async def list_subjects(session: AsyncSession = Depends(get_session)):
    rows = await subjects_repo.list_subjects_with_group_ids(session)
    return [
        SubjectOut(id=s.id, name=s.name, description=s.description, group_ids=gids)
        for (s, gids) in rows
    ]

@app.get("/api/subjects/{subject_id}", response_model=SubjectOutFull)
async def get_subject(subject_id: int, session: AsyncSession = Depends(get_session)):
    row = await subjects_repo.get_subject_with_group_ids(session, subject_id)
    if not row:
        raise HTTPException(status_code=404, detail="Subject not found")
    s, gids = row
    return SubjectOutFull(id=s.id, name=s.name, description=s.description, group_ids=gids)

@app.post("/api/subjects", response_model=SubjectOut, status_code=status.HTTP_201_CREATED)
async def create_subject(payload: SubjectCreateIn, session: AsyncSession = Depends(get_session)):
    subj = await subjects_repo.create_subject(session, name=payload.name, description=payload.description)
    # гарантируем id, если репозиторий не делает flush сам
    await session.flush()
    if payload.group_ids is not None:  # важно: можно и очищать
        await subjects_repo.set_subject_groups(session, subject_id=subj.id, group_ids=payload.group_ids)
    await session.commit()
    s, gids = await subjects_repo.get_subject_with_group_ids(session, subj.id)
    return SubjectOut(id=s.id, name=s.name, description=s.description, group_ids=gids)

@app.patch("/api/subjects/{subject_id}",
           dependencies=[Depends(require_roles("admin", "teacher"))],
           response_model=SubjectOutFull)
async def patch_subject(subject_id: int, body: SubjectPatchIn, session: AsyncSession = Depends(get_session)):
    subj = await subjects_repo.update_subject(
        session,
        subject_id=subject_id,
        name=body.name,
        description=body.description,
        group_ids=body.group_ids,  # репозиторий внутри вызовет set_subject_groups
    )
    if not subj:
        raise HTTPException(status_code=404, detail="Subject not found")
    await session.commit()
    s, gids = await subjects_repo.get_subject_with_group_ids(session, subject_id)
    return SubjectOutFull(id=s.id, name=s.name, description=s.description, group_ids=gids)

@app.get("/api/subjects/{subject_id}/lessons",
         dependencies=[Depends(require_roles("admin", "teacher"))],
         response_model=list[LessonListItemOut])
async def subject_lessons(subject_id: int, session: AsyncSession = Depends(get_session)):
    rows = await lessons_repo.list_subject_lessons_with_group_ids(session, subject_id)
    return [
        LessonListItemOut(
            id=l.id, subject_id=l.subject_id, title=l.title,
            status=l.status, publish_at=l.publish_at, group_ids=gids
        )
        for (l, gids) in rows
    ]

# -------- Lessons --------
@app.post("/api/lessons", response_model=LessonOut, status_code=status.HTTP_201_CREATED)
async def create_lesson(payload: LessonCreateIn, session: AsyncSession = Depends(get_session)):
    blocks = [{"type": b.type, "text": b.text, "image_url": b.image_url, "caption": b.caption} for b in getattr(payload, "blocks", [])]

    try:
        lesson = await lessons_repo.create_lesson(
            session,
            subject_id=payload.subject_id,
            title=payload.title,
            blocks=blocks,              # если pdf_url — список уже пуст
            publish=payload.publish,
            publish_at=payload.publish_at,
        )
    except lessons_repo.SubjectNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except lessons_repo.PayloadInvalidError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # NEW: если это «pdf-урок» — проставим ссылку
    if payload.pdf_url:
        db_lesson = await session.get(Lesson, lesson.id)
        db_lesson.pdf_url = payload.pdf_url
        lesson.pdf_url = payload.pdf_url

    if payload.group_ids is not None:
        await lessons_repo.set_lesson_groups(session, lesson_id=lesson.id, group_ids=payload.group_ids)
    if payload.user_tg_ids:
        ids: list[int] = []
        for tg in payload.user_tg_ids:
            u = await users_repo.ensure_user(session, tg)
            ids.append(u.id)
        await lessons_repo.update_lesson(session, lesson_id=lesson.id, user_ids=ids)

    await session.commit()

    # NEW: вернём pdf_url тоже
    return LessonOut(
        id=lesson.id, subject_id=lesson.subject_id, title=lesson.title,
        status=lesson.status, publish_at=lesson.publish_at, pdf_url=lesson.pdf_url
    )


@app.patch("/api/lessons/{lesson_id}",
           dependencies=[Depends(require_roles("admin", "teacher"))],
           response_model=LessonDetailOut)
async def patch_lesson(lesson_id: int, body: LessonPatchIn, session: AsyncSession = Depends(get_session)):
    blocks_payload = None
    if body.blocks is not None:
        blocks_payload = [{"type": b.type, "text": b.text, "image_url": b.image_url, "caption": b.caption} for b in body.blocks]

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
        db_lesson = await session.get(Lesson, lesson_id)
        db_lesson.pdf_url = body.pdf_url
    
    await session.commit()

    l, gids = await lessons_repo.get_lesson_detail(session, lesson_id)
    blocks = [
        LessonBlockOut(position=b.position, type=b.type, text=b.text, image_url=b.image_url, caption=b.caption)
        for b in l.blocks
    ]
    return LessonDetailOut(
        id=l.id, subject_id=l.subject_id, title=l.title, status=l.status, publish_at=l.publish_at,
        blocks=blocks, group_ids=gids, pdf_url=l.pdf_url
    )

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


@app.post("/api/lessons/{lesson_id}/pdf", status_code=201)
async def upload_lesson_pdf(lesson_id: int, file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    # проверим наличие урока
    l = await session.get(Lesson, lesson_id)
    if not l:
        raise HTTPException(status_code=404, detail="Lesson not found")

    # проверим тип
    if file.content_type not in ("application/pdf",):
        raise HTTPException(status_code=415, detail="Only PDF is allowed")

    # сохраним файл
    folder = Path("uploads/pdfs")
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid4().hex}.pdf"
    fpath = folder / fname

    content = await file.read()
    with open(fpath, "wb") as f:
        f.write(content)

    # проставим ссылку
    l.pdf_url = f"/files/pdfs/{fname}"
    await session.commit()

    return {"status": "ok", "pdf_url": l.pdf_url}


# -------- Roles & Groups --------
@app.get("/api/roles", dependencies=[Depends(require_roles("admin", "teacher"))])
async def list_roles(session: AsyncSession = Depends(get_session)):
    rows = await users_repo.list_roles_with_members(session)
    return [
        {"key": role.code, "title": role.name,
         "members": [{"tg_id": u.telegram_id, "first_name": u.first_name, "last_name": u.last_name,
                      "username": u.username, "avatar_url": u.avatar_url} for u in members]}
        for role, members in rows
    ]

@app.post("/api/roles/{role}/members", dependencies=[Depends(require_roles("admin", "teacher"))])
async def add_role_member(role: str, body: RoleMemberIn, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.add_role_to_user(session, body.tg_id, role)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    await session.commit()
    return {"status": "ok"}

@app.delete("/api/roles/{role}/members/{tg_id}", dependencies=[Depends(require_roles("admin", "teacher"))])
async def remove_role_member(role: str, tg_id: int, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.remove_role_from_user(session, tg_id, role)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    await session.commit()
    return {"status": "ok"}

@app.get("/api/groups", dependencies=[Depends(require_roles("admin", "teacher"))])
async def list_groups(session: AsyncSession = Depends(get_session)):
    groups = await users_repo.list_groups_with_members(session)
    return [{"id": g.id, "name": g.name,
             "members": [{"tg_id": u.telegram_id, "first_name": u.first_name, "last_name": u.last_name,
                          "username": u.username, "avatar_url": u.avatar_url} for u in g.members]}
            for g in groups]

@app.post("/api/groups/{group_id}/members", dependencies=[Depends(require_roles("admin", "teacher"))])
async def add_group_member(group_id: int, body: GroupMemberIn, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.add_user_to_group(session, body.tg_id, group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User or group not found")
    await session.commit()
    return {"status": "ok"}

@app.delete("/api/groups/{group_id}/members/{tg_id}", dependencies=[Depends(require_roles("admin", "teacher"))])
async def remove_group_member(group_id: int, tg_id: int, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.remove_user_from_group(session, tg_id, group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User or group not found")
    await session.commit()
    return {"status": "ok"}

@app.post("/api/groups", dependencies=[Depends(require_roles("admin", "teacher"))], status_code=201)
async def create_group(body: GroupCreateIn, session: AsyncSession = Depends(get_session)):
    try:
        g = await users_repo.create_group(session, name=body.name)
    except users_repo.GroupAlreadyExistsError:
        raise HTTPException(status_code=409, detail="Group already exists")
    await session.commit()
    return {"id": g.id, "name": g.name}

@app.patch("/api/groups/{group_id}", dependencies=[Depends(require_roles("admin", "teacher"))])
async def patch_group(group_id: int, body: GroupPatchIn, session: AsyncSession = Depends(get_session)):
    g = await session.get(Group, group_id)  # ← напрямую из models
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    g.name = body.name
    await session.flush()
    await session.commit()
    return {"id": g.id, "name": g.name}

@app.delete("/api/groups/{group_id}", dependencies=[Depends(require_roles("admin", "teacher"))], status_code=204)
async def delete_group(group_id: int, session: AsyncSession = Depends(get_session)):
    g = await session.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    await session.delete(g)
    await session.commit()
    return None
