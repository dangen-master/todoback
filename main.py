# main.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    select, delete, insert, update, and_, or_, func
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ===== ваши модели =====
from models import (
    Base,
    User, Group, GroupMember,
    Role, UserRole,
    Subject, SubjectAccessGroup,
    Lesson, LessonBlock, LessonAccessGroup,
)

# ================= DB =================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./db.sqlite3")
engine = create_async_engine(DATABASE_URL, future=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session

# создаём таблицы при старте (для sqlite/демо)
app = FastAPI(title="petrocollege api")
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ================= CORS =================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============== УТИЛИТЫ/SEC ==============
def to_ids(v) -> list[int]:
    if not isinstance(v, list):
        return []
    return [int(x) for x in v if isinstance(x, (int, str)) and str(x).isdigit()]

async def user_roles_keys(session: AsyncSession, tg_id: int) -> list[str]:
    q = (
        select(Role.key)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_tg_id == tg_id)
    )
    rows = await session.execute(q)
    return [r[0] for r in rows.all()]

def require_roles(*need: str):
    async def dep(
        session: Annotated[AsyncSession, Depends(get_session)],
        x_tg: Optional[str] = Header(None, alias="X-Debug-Tg-Id"),
    ):
        if not x_tg or not str(x_tg).isdigit():
            raise HTTPException(status_code=403, detail="Missing X-Debug-Tg-Id")
        tg_id = int(x_tg)
        have = set([k.lower() for k in await user_roles_keys(session, tg_id)])
        if not any(n.lower() in have for n in need):
            raise HTTPException(status_code=403, detail="Forbidden")
        return tg_id
    return dep

async def current_tg_id(
    x_tg: Optional[str] = Header(None, alias="X-Debug-Tg-Id"),
    tg_id_q: Optional[int] = Query(None, alias="tg_id"),
) -> Optional[int]:
    if x_tg and str(x_tg).isdigit():
        return int(x_tg)
    if tg_id_q:
        return tg_id_q
    return None

# ============== Pydantic схемы ==============
# users
class EnsureUserIn(BaseModel):
    tg_id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None

class UserShort(BaseModel):
    tg_id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None

class ProfileOut(UserShort):
    photo_url: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    groups: list[dict] = Field(default_factory=list)

# groups
class GroupCreateIn(BaseModel):
    name: str = Field(min_length=1)

class GroupRenameIn(BaseModel):
    name: str = Field(min_length=1)

class GroupOut(BaseModel):
    id: int
    name: str
    members: list[UserShort] = Field(default_factory=list)

# subjects
class SubjectIn(BaseModel):
    name: str
    description: Optional[str] = None
    group_ids: list[int] = Field(default_factory=list)

class SubjectPatchIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    group_ids: Optional[list[int]] = None

class SubjectOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    group_ids: list[int] = Field(default_factory=list)

# lessons
class BlockIn(BaseModel):
    type: str = Field(default="text")
    text: Optional[str] = None
    image_url: Optional[str] = None
    caption: Optional[str] = None

class LessonCreateIn(BaseModel):
    subject_id: int
    title: str
    publish: bool = False
    publish_at: Optional[datetime] = None
    blocks: list[BlockIn] = Field(default_factory=list)
    group_ids: list[int] = Field(default_factory=list)

class LessonPatchIn(BaseModel):
    title: Optional[str] = None
    publish: Optional[bool] = None
    publish_at: Optional[datetime] = None
    blocks: Optional[list[BlockIn]] = None
    group_ids: Optional[list[int]] = None

class LessonOut(BaseModel):
    id: int
    subject_id: int
    title: str
    status: str
    publish_at: Optional[datetime] = None

class LessonDetailOut(LessonOut):
    blocks: list[BlockIn] = Field(default_factory=list)
    group_ids: list[int] = Field(default_factory=list)

# ============== helpers: groups M2M ==============
async def set_subject_groups(session: AsyncSession, subject_id: int, group_ids: list[int]) -> None:
    ids = set(to_ids(group_ids))
    if ids:
        rows = await session.execute(select(Group.id).where(Group.id.in_(ids)))
        ids = {r[0] for r in rows.all()}
    await session.execute(delete(SubjectAccessGroup).where(SubjectAccessGroup.subject_id == subject_id))
    if ids:
        await session.execute(
            insert(SubjectAccessGroup),
            [{"subject_id": subject_id, "group_id": gid} for gid in ids]
        )

async def get_subject_group_ids(session: AsyncSession, subject_id: int) -> list[int]:
    rows = await session.execute(
        select(SubjectAccessGroup.group_id).where(SubjectAccessGroup.subject_id == subject_id)
    )
    return [r[0] for r in rows.all()]

async def set_lesson_groups(session: AsyncSession, lesson_id: int, group_ids: list[int]) -> None:
    ids = set(to_ids(group_ids))
    if ids:
        rows = await session.execute(select(Group.id).where(Group.id.in_(ids)))
        ids = {r[0] for r in rows.all()}
    await session.execute(delete(LessonAccessGroup).where(LessonAccessGroup.lesson_id == lesson_id))
    if ids:
        await session.execute(
            insert(LessonAccessGroup),
            [{"lesson_id": lesson_id, "group_id": gid} for gid in ids]
        )

async def get_lesson_group_ids(session: AsyncSession, lesson_id: int) -> list[int]:
    rows = await session.execute(
        select(LessonAccessGroup.group_id).where(LessonAccessGroup.lesson_id == lesson_id)
    )
    return [r[0] for r in rows.all()]

# ============== USERS/APIs ==============
@app.post("/api/user/ensure", response_model=ProfileOut)
async def ensure_user(payload: EnsureUserIn, session: Annotated[AsyncSession, Depends(get_session)]):
    u = await session.get(User, payload.tg_id)
    if not u:
        u = User(
            tg_id=payload.tg_id,
            first_name=payload.first_name,
            last_name=payload.last_name,
            username=payload.username,
            photo_url=payload.photo_url,
        )
        session.add(u)
        await session.flush()
    else:
        # легкий апдейт
        u.first_name = payload.first_name
        u.last_name = payload.last_name
        u.username = payload.username
        u.photo_url = payload.photo_url

    # соберём профиль
    roles_q = (
        select(Role.key)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_tg_id == u.tg_id)
    )
    roles = [r[0] for r in (await session.execute(roles_q)).all()]

    gq = (
        select(Group.id, Group.name)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_tg_id == u.tg_id)
    )
    groups = [{"id": r[0], "name": r[1]} for r in (await session.execute(gq)).all()]

    await session.commit()
    return ProfileOut(
        tg_id=u.tg_id, first_name=u.first_name, last_name=u.last_name,
        username=u.username, photo_url=u.photo_url, roles=roles, groups=groups
    )

@app.get("/api/user/profile/{tg_id}", response_model=ProfileOut)
async def user_profile(tg_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    u = await session.get(User, tg_id)
    if not u:
        raise HTTPException(404, "User not found")
    roles = await user_roles_keys(session, tg_id)
    rows = await session.execute(
        select(Group.id, Group.name).join(GroupMember).where(GroupMember.user_tg_id == tg_id)
    )
    groups = [{"id": r[0], "name": r[1]} for r in rows.all()]
    return ProfileOut(
        tg_id=u.tg_id, first_name=u.first_name, last_name=u.last_name,
        username=u.username, photo_url=u.photo_url, roles=roles, groups=groups
    )

@app.get("/api/users", response_model=list[UserShort])
async def list_users(session: Annotated[AsyncSession, Depends(get_session)]):
    rows = await session.execute(select(User))
    return [UserShort(tg_id=u.tg_id, first_name=u.first_name, last_name=u.last_name, username=u.username)
            for u in rows.scalars().all()]

# ============== ROLES ==============
@app.get("/api/roles")
async def list_roles(session: Annotated[AsyncSession, Depends(get_session)]):
    rows = await session.execute(select(Role))
    roles = []
    for r in rows.scalars().all():
        mem_rows = await session.execute(
            select(User.tg_id, User.first_name, User.last_name, User.username)
            .join(UserRole, UserRole.user_tg_id == User.tg_id)
            .where(UserRole.role_id == r.id)
        )
        members = [
            {"tg_id": m[0], "first_name": m[1], "last_name": m[2], "username": m[3]}
        for m in mem_rows.all()]
        roles.append({"key": r.key, "title": getattr(r, "name", r.key), "members": members})
    return roles

class RoleMemberIn(BaseModel):
    tg_id: int

@app.post("/api/roles/{role_key}/members", dependencies=[Depends(require_roles("admin","teacher"))])
async def add_role_member(role_key: str, body: RoleMemberIn,
                          session: Annotated[AsyncSession, Depends(get_session)]):
    role = (await session.execute(select(Role).where(Role.key == role_key))).scalar_one_or_none()
    if not role: raise HTTPException(404, "Role not found")
    # ensure user
    user = await session.get(User, body.tg_id)
    if not user: raise HTTPException(404, "User not found")
    # check exists
    exists = await session.execute(
        select(UserRole).where(and_(UserRole.user_tg_id == body.tg_id, UserRole.role_id == role.id))
    )
    if not exists.scalar_one_or_none():
        session.add(UserRole(user_tg_id=body.tg_id, role_id=role.id))
        await session.commit()
    return {"ok": True}

@app.delete("/api/roles/{role_key}/members/{tg_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def remove_role_member(role_key: str, tg_id: int,
                             session: Annotated[AsyncSession, Depends(get_session)]):
    role = (await session.execute(select(Role).where(Role.key == role_key))).scalar_one_or_none()
    if not role: raise HTTPException(404, "Role not found")
    await session.execute(
        delete(UserRole).where(and_(UserRole.user_tg_id == tg_id, UserRole.role_id == role.id))
    )
    await session.commit()
    return {"ok": True}

# ============== GROUPS ==============
@app.get("/api/groups", response_model=list[GroupOut])
async def list_groups(session: Annotated[AsyncSession, Depends(get_session)]):
    groups = (await session.execute(select(Group))).scalars().all()
    result: list[GroupOut] = []
    for g in groups:
        mem_rows = await session.execute(
            select(User.tg_id, User.first_name, User.last_name, User.username)
            .join(GroupMember, GroupMember.user_tg_id == User.tg_id)
            .where(GroupMember.group_id == g.id)
        )
        members = [UserShort(tg_id=m[0], first_name=m[1], last_name=m[2], username=m[3]) for m in mem_rows.all()]
        result.append(GroupOut(id=g.id, name=g.name, members=members))
    return result

@app.post("/api/groups", dependencies=[Depends(require_roles("admin","teacher"))], status_code=201)
async def create_group(body: GroupCreateIn, session: Annotated[AsyncSession, Depends(get_session)]):
    g = Group(name=body.name)
    session.add(g)
    await session.commit()
    return {"id": g.id, "name": g.name}

@app.patch("/api/groups/{group_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def patch_group(group_id: int, body: GroupRenameIn, session: Annotated[AsyncSession, Depends(get_session)]):
    g = await session.get(Group, group_id)
    if not g: raise HTTPException(404, "Group not found")
    g.name = body.name
    await session.commit()
    return {"id": g.id, "name": g.name}

@app.delete("/api/groups/{group_id}", dependencies=[Depends(require_roles("admin","teacher"))], status_code=204)
async def delete_group(group_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    await session.execute(delete(Group).where(Group.id == group_id))
    await session.commit()
    return None

class GroupMemberIn(BaseModel):
    tg_id: int

@app.post("/api/groups/{group_id}/members", dependencies=[Depends(require_roles("admin","teacher"))])
async def add_group_member(group_id: int, body: GroupMemberIn,
                           session: Annotated[AsyncSession, Depends(get_session)]):
    g = await session.get(Group, group_id)
    if not g: raise HTTPException(404, "Group not found")
    u = await session.get(User, body.tg_id)
    if not u: raise HTTPException(404, "User not found")
    exists = await session.execute(
        select(GroupMember).where(and_(GroupMember.group_id == group_id, GroupMember.user_tg_id == body.tg_id))
    )
    if not exists.scalar_one_or_none():
        session.add(GroupMember(group_id=group_id, user_tg_id=body.tg_id))
        await session.commit()
    return {"ok": True}

@app.delete("/api/groups/{group_id}/members/{tg_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def remove_group_member(group_id: int, tg_id: int,
                              session: Annotated[AsyncSession, Depends(get_session)]):
    await session.execute(
        delete(GroupMember).where(and_(GroupMember.group_id == group_id, GroupMember.user_tg_id == tg_id))
    )
    await session.commit()
    return {"ok": True}

# ============== SUBJECTS ==============
@app.post("/api/subjects", dependencies=[Depends(require_roles("admin","teacher"))], status_code=201)
async def create_subject(body: SubjectIn, session: Annotated[AsyncSession, Depends(get_session)]):
    s = Subject(name=body.name, description=body.description)
    session.add(s)
    await session.flush()  # нужен id
    await set_subject_groups(session, s.id, body.group_ids)
    await session.commit()
    gids = await get_subject_group_ids(session, s.id)
    return {"id": s.id, "name": s.name, "description": s.description, "group_ids": gids}

@app.get("/api/subjects", response_model=list[SubjectOut])
async def list_subjects(session: Annotated[AsyncSession, Depends(get_session)]):
    items = (await session.execute(select(Subject))).scalars().all()
    out: list[SubjectOut] = []
    for s in items:
        gids = await get_subject_group_ids(session, s.id)
        out.append(SubjectOut(id=s.id, name=s.name, description=s.description, group_ids=gids))
    return out

@app.get("/api/subjects/{subject_id}", response_model=SubjectOut)
async def get_subject(subject_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    s = await session.get(Subject, subject_id)
    if not s: raise HTTPException(404, "Subject not found")
    gids = await get_subject_group_ids(session, s.id)
    return SubjectOut(id=s.id, name=s.name, description=s.description, group_ids=gids)

@app.patch("/api/subjects/{subject_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def patch_subject(subject_id: int, body: SubjectPatchIn, session: Annotated[AsyncSession, Depends(get_session)]):
    s = await session.get(Subject, subject_id)
    if not s: raise HTTPException(404, "Subject not found")
    if body.name is not None: s.name = body.name
    if body.description is not None: s.description = body.description
    await session.flush()
    if body.group_ids is not None:
        await set_subject_groups(session, s.id, body.group_ids)
    await session.commit()
    gids = await get_subject_group_ids(session, s.id)
    return {"id": s.id, "name": s.name, "description": s.description, "group_ids": gids}

# список всех уроков предмета (для учителей/админов)
@app.get("/api/subjects/{subject_id}/lessons", dependencies=[Depends(require_roles("admin","teacher"))])
async def subject_lessons(subject_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    rows = await session.execute(select(Lesson).where(Lesson.subject_id == subject_id).order_by(Lesson.id.desc()))
    items = rows.scalars().all()
    out = []
    for l in items:
        out.append({
            "id": l.id, "subject_id": l.subject_id, "title": l.title,
            "status": l.status, "publish_at": l.publish_at
        })
    return out

# ============== LESSONS ==============
def blocks_to_models(lesson_id: int, blocks: list[BlockIn]) -> list[LessonBlock]:
    m: list[LessonBlock] = []
    pos = 1
    for b in blocks or []:
        m.append(LessonBlock(
            lesson_id=lesson_id, position=pos, type=b.type,
            text=b.text, image_url=b.image_url, caption=b.caption
        ))
        pos += 1
    return m

@app.post("/api/lessons", dependencies=[Depends(require_roles("admin","teacher"))], status_code=201)
async def create_lesson(body: LessonCreateIn, session: Annotated[AsyncSession, Depends(get_session)]):
    subj = await session.get(Subject, body.subject_id)
    if not subj: raise HTTPException(404, "Subject not found")
    l = Lesson(
        subject_id=body.subject_id, title=body.title,
        status="published" if body.publish else "draft",
        publish_at=body.publish_at
    )
    session.add(l)
    await session.flush()
    # blocks
    if body.blocks:
        for b in blocks_to_models(l.id, body.blocks):
            session.add(b)
    # groups
    await set_lesson_groups(session, l.id, body.group_ids)
    await session.commit()
    return {"id": l.id}

@app.get("/api/lessons/{lesson_id}", response_model=LessonDetailOut)
async def get_lesson(lesson_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    l = await session.get(Lesson, lesson_id)
    if not l: raise HTTPException(404, "Lesson not found")
    gids = await get_lesson_group_ids(session, l.id)
    blocks = [
        BlockIn(type=b.type, text=b.text, image_url=b.image_url, caption=b.caption)
        for b in sorted(l.blocks, key=lambda x: x.position)
    ]
    return LessonDetailOut(
        id=l.id, subject_id=l.subject_id, title=l.title,
        status=l.status, publish_at=l.publish_at, blocks=blocks, group_ids=gids
    )

@app.patch("/api/lessons/{lesson_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def patch_lesson(lesson_id: int, body: LessonPatchIn, session: Annotated[AsyncSession, Depends(get_session)]):
    l = await session.get(Lesson, lesson_id)
    if not l: raise HTTPException(404, "Lesson not found")
    if body.title is not None: l.title = body.title
    if body.publish is not None: l.status = "published" if body.publish else "draft"
    if body.publish_at is not None: l.publish_at = body.publish_at
    if body.blocks is not None:
        await session.execute(delete(LessonBlock).where(LessonBlock.lesson_id == lesson_id))
        for b in blocks_to_models(lesson_id, body.blocks):
            session.add(b)
    if body.group_ids is not None:
        await set_lesson_groups(session, lesson_id, body.group_ids)
    await session.commit()
    return {"status": "ok"}

# доступные уроки пользователю
@app.get("/api/lessons/accessible/{tg_id}", response_model=list[LessonOut])
async def accessible_lessons(tg_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    # группы пользователя
    rows = await session.execute(
        select(GroupMember.group_id).where(GroupMember.user_tg_id == tg_id)
    )
    user_gids = [r[0] for r in rows.all()]

    now = datetime.now(timezone.utc)

    # условия доступа по группам через предмет или урок
    cond_pub = and_(
        Lesson.status == "published",
        or_(Lesson.publish_at.is_(None), Lesson.publish_at <= now),
    )

    cond_by_lesson_group = Lesson.id.in_(
        select(LessonAccessGroup.lesson_id).where(LessonAccessGroup.group_id.in_(user_gids))
    )

    cond_by_subject_group = Lesson.subject_id.in_(
        select(SubjectAccessGroup.subject_id).where(SubjectAccessGroup.group_id.in_(user_gids))
    )

    q = (
        select(Lesson)
        .where(and_(cond_pub, or_(cond_by_lesson_group, cond_by_subject_group)))
        .order_by(Lesson.id.desc())
    )
    lessons = (await session.execute(q)).scalars().all()
    return [LessonOut(id=l.id, subject_id=l.subject_id, title=l.title, status=l.status, publish_at=l.publish_at)
            for l in lessons]
