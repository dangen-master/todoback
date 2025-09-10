from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from models import async_session, init_db
from repositories import users as users_repo
from repositories import subjects as subjects_repo
from repositories import lessons as lessons_repo


# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app_: FastAPI):
    await init_db()
    print("API is ready")
    yield


app = FastAPI(title="Edu MiniApp API", lifespan=lifespan)

FRONTEND_ORIGIN = "https://telegrammapp-44890.web.app"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- DB session dependency ----------
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


# ---------- Schemas ----------
class EnsureUserIn(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None


class SubjectCreateIn(BaseModel):
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    created_by_tg: Optional[int] = None


class SubjectOut(BaseModel):
    id: int
    code: Optional[str] = None
    name: str
    description: Optional[str] = None


class LessonBlockIn(BaseModel):
    type: str = Field(..., pattern="^(text|image)$")
    text: Optional[str] = None
    image_url: Optional[str] = None
    caption: Optional[str] = None

    @field_validator("text")
    @classmethod
    def validate_text_for_type(cls, v, info):
        data = info.data
        if data.get("type") == "text" and not v:
            raise ValueError("text is required when type='text'")
        return v

    @field_validator("image_url")
    @classmethod
    def validate_image_for_type(cls, v, info):
        data = info.data
        if data.get("type") == "image" and not v:
            raise ValueError("image_url is required when type='image'")
        return v


class LessonCreateIn(BaseModel):
    subject_id: int
    title: str
    publish: bool = True
    blocks: list[LessonBlockIn]
    created_by_tg: Optional[int] = None


class LessonOut(BaseModel):
    id: int
    subject_id: int
    title: str


class GrantUsersIn(BaseModel):
    lesson_id: int
    user_tg_ids: list[int]


class GrantGroupsIn(BaseModel):
    lesson_id: int
    group_ids: list[int]


# ---------- Endpoints ----------
@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "Сервер работает"}


@app.post("/api/user/ensure")
async def api_ensure_user(data: EnsureUserIn, session: AsyncSession = Depends(get_session)):
    user = await users_repo.ensure_user(
        session,
        data.tg_id,
        username=data.username,
        first_name=data.first_name,
        last_name=data.last_name,
        avatar_url=data.avatar_url,
    )
    await session.commit()
    return {"id": user.id, "tg_id": user.telegram_id}


@app.get("/api/subjects", response_model=list[SubjectOut])
async def list_subjects(session: AsyncSession = Depends(get_session)):
    rows = await subjects_repo.list_subjects(session)
    return [SubjectOut(id=s.id, code=s.code, name=s.name, description=s.description) for s in rows]


@app.post("/api/subjects", response_model=SubjectOut, status_code=status.HTTP_201_CREATED)
async def create_subject(payload: SubjectCreateIn, session: AsyncSession = Depends(get_session)):
    created_by_id = None
    if payload.created_by_tg is not None:
        creator = await users_repo.ensure_user(session, payload.created_by_tg)
        created_by_id = creator.id
    subj = await subjects_repo.create_subject(
        session,
        name=payload.name,
        code=payload.code,
        description=payload.description,
        created_by=created_by_id,
    )
    await session.commit()
    return SubjectOut(id=subj.id, code=subj.code, name=subj.name, description=subj.description)


@app.post("/api/lessons", response_model=LessonOut, status_code=status.HTTP_201_CREATED)
async def create_lesson(payload: LessonCreateIn, session: AsyncSession = Depends(get_session)):
    created_by_id = None
    if payload.created_by_tg is not None:
        creator = await users_repo.ensure_user(session, payload.created_by_tg)
        created_by_id = creator.id

    blocks = [
        {"type": b.type, "text": b.text, "image_url": b.image_url, "caption": b.caption}
        for b in payload.blocks
    ]

    try:
        lesson = await lessons_repo.create_lesson(
            session,
            subject_id=payload.subject_id,
            title=payload.title,
            blocks=blocks,
            publish=payload.publish,
            created_by=created_by_id,
        )
    except lessons_repo.SubjectNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except lessons_repo.PayloadInvalidError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await session.commit()
    return LessonOut(id=lesson.id, subject_id=lesson.subject_id, title=lesson.title)


@app.get("/api/lessons/accessible/{tg_id}", response_model=list[LessonOut])
async def accessible_lessons(tg_id: int, session: AsyncSession = Depends(get_session)):
    user = await users_repo.get_user_by_tg(session, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    lessons = await lessons_repo.get_accessible_lessons_for_user(session, user_id=user.id)
    return [LessonOut(id=l.id, subject_id=l.subject_id, title=l.title) for l in lessons]


@app.post("/api/access/grant/users")
async def grant_access_users(body: GrantUsersIn, session: AsyncSession = Depends(get_session)):
    if not body.user_tg_ids:
        return {"updated": 0}
    user_ids: list[int] = []
    for tg in body.user_tg_ids:
        u = await users_repo.ensure_user(session, tg)
        user_ids.append(u.id)

    updated = await lessons_repo.grant_access_to_users(session, lesson_id=body.lesson_id, user_ids=user_ids)
    await session.commit()
    return {"updated": updated}


@app.post("/api/access/grant/groups")
async def grant_access_groups(body: GrantGroupsIn, session: AsyncSession = Depends(get_session)):
    updated = await lessons_repo.grant_access_to_groups(session, lesson_id=body.lesson_id, group_ids=body.group_ids)
    await session.commit()
    return {"updated": updated}
