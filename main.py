from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Optional, Callable, Awaitable, Any

from fastapi import FastAPI, Depends, HTTPException, status, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, FieldValidationInfo
from sqlalchemy.ext.asyncio import AsyncSession

from models import async_session, init_db
from repositories import users as users_repo
from repositories import subjects as subjects_repo
from repositories import lessons as lessons_repo


# ---------- DB session dependency ----------
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


# ---------- Auth / RBAC ----------
async def get_current_user(
    session: AsyncSession = Depends(get_session),
    x_debug_tg_id: Optional[int] = Header(None, alias="X-Debug-Tg-Id"),
):
    """
    DEV-версия авторизации: заголовок X-Debug-Tg-Id.
    Для прод: подставить разбор X-Telegram-Init-Data.
    """
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
    def validate_text_for_type(cls, v, info: FieldValidationInfo):
        if (info.data or {}).get("type") == "text" and not v:
            raise ValueError("text is required when type='text'")
        return v

    @field_validator("image_url")
    def validate_image_for_type(cls, v, info: FieldValidationInfo):
        if (info.data or {}).get("type") == "image" and not v:
            raise ValueError("image_url is required when type='image'")
        return v

class LessonCreateIn(BaseModel):
    subject_id: int
    title: str
    publish: bool = True
    blocks: list[LessonBlockIn]
    created_by_tg: Optional[int] = None
    # новые поля: сразу выдать доступ после создания
    group_ids: Optional[list[int]] = None
    user_tg_ids: Optional[list[int]] = None

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

class MemberOut(BaseModel):
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

class RoleMemberIn(BaseModel):
    tg_id: int

class GroupMemberIn(BaseModel):
    tg_id: int

class GroupCreateIn(BaseModel):
    code: str
    name: str


# ---------- Endpoints ----------
@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "Сервер работает"}


# Users — admin|teacher: список c ролями/группами
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


# Ensure user -> возвратим полный профиль
@app.post("/api/user/ensure", response_model=UserProfileOut)
async def api_ensure_user(data: EnsureUserIn, session: AsyncSession = Depends(get_session)):
    await users_repo.ensure_user(
        session,
        data.tg_id,
        username=data.username,
        first_name=data.first_name,
        last_name=data.last_name,
        avatar_url=data.avatar_url,
    )
    await session.commit()
    prof = await users_repo.get_user_profile(session, data.tg_id)
    return UserProfileOut(
        tg_id=prof.telegram_id,
        username=prof.username,
        first_name=prof.first_name,
        last_name=prof.last_name,
        avatar_url=prof.avatar_url,
        roles=[r.code for r in prof.roles],
        groups=[{"id": g.id, "name": g.name} for g in prof.groups],
    )


# Профиль пользователя
@app.get("/api/user/profile/{tg_id}", response_model=UserProfileOut)
async def get_user_profile(tg_id: int, session: AsyncSession = Depends(get_session)):
    user = await users_repo.get_user_profile(session, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfileOut(
        tg_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        avatar_url=user.avatar_url,
        roles=[r.code for r in user.roles],
        groups=[{"id": g.id, "name": g.name} for g in user.groups],
    )


# Subjects
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


# Lessons
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

    # ← НОВОЕ: сразу выдаём доступ урока
    if payload.group_ids:
        await lessons_repo.grant_access_to_groups(
            session, lesson_id=lesson.id, group_ids=payload.group_ids
        )

    if payload.user_tg_ids:
        user_ids: list[int] = []
        for tg in payload.user_tg_ids:
            u = await users_repo.ensure_user(session, tg)
            user_ids.append(u.id)
        await lessons_repo.grant_access_to_users(
            session, lesson_id=lesson.id, user_ids=user_ids
        )

    await session.commit()
    return LessonOut(id=lesson.id, subject_id=lesson.subject_id, title=lesson.title)


@app.get("/api/lessons/accessible/{tg_id}", response_model=list[LessonOut])
async def accessible_lessons(tg_id: int, session: AsyncSession = Depends(get_session)):
    user = await users_repo.get_user_by_tg(session, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    lessons = await lessons_repo.get_accessible_lessons_for_user(session, user_id=user.id)
    return [LessonOut(id=l.id, subject_id=l.subject_id, title=l.title) for l in lessons]


# Access grants
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


# Roles (admin|teacher)
@app.get("/api/roles", dependencies=[Depends(require_roles("admin", "teacher"))])
async def list_roles(session: AsyncSession = Depends(get_session)):
    rows = await users_repo.list_roles_with_members(session)
    return [
        {
            "key": role.code,
            "title": role.name,
            "members": [
                {
                    "tg_id": u.telegram_id,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "username": u.username,
                    "avatar_url": u.avatar_url,
                } for u in members
            ],
        }
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


# Groups (admin|teacher)
@app.get("/api/groups", dependencies=[Depends(require_roles("admin", "teacher"))])
async def list_groups(session: AsyncSession = Depends(get_session)):
    groups = await users_repo.list_groups_with_members(session)
    return [
        {
            "id": g.id,
            "name": g.name,
            "members": [
                {
                    "tg_id": u.telegram_id,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "username": u.username,
                    "avatar_url": u.avatar_url,
                } for u in g.members
            ],
        } for g in groups
    ]

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
        g = await users_repo.create_group(session, code=body.code, name=body.name)
    except users_repo.GroupAlreadyExistsError:
        raise HTTPException(status_code=409, detail="Group code already exists")
    await session.commit()
    return {"id": g.id, "code": g.code, "name": g.name}

