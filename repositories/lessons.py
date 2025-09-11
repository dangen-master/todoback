from typing import Sequence

from sqlalchemy import select, func, and_, or_, literal
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Lesson, LessonBlock, Subject,
    LessonAccessUser, LessonAccessGroup,
    Group, GroupMember,
)

class SubjectNotFoundError(Exception): ...
class PayloadInvalidError(Exception): ...

async def create_lesson(
    session: AsyncSession,
    *,
    subject_id: int,
    title: str,
    blocks: Sequence[dict],
    publish: bool = True,
) -> "Lesson":
    subj = await session.get(Subject, subject_id)
    if not subj:
        raise SubjectNotFoundError("Subject not found")

    for i, b in enumerate(blocks, start=1):
        t = b.get("type")
        if t not in ("text", "image"):
            raise PayloadInvalidError(f"Block #{i}: invalid type {t!r}")
        if t == "text" and not (b.get("text") and str(b.get("text")).strip()):
            raise PayloadInvalidError(f"Block #{i}: 'text' is required for type='text'")
        if t == "image" and not (b.get("image_url") and str(b.get("image_url")).strip()):
            raise PayloadInvalidError(f"Block #{i}: 'image_url' is required for type='image'")

    lesson = Lesson(
        subject_id=subject_id,
        title=title,
        status="published" if publish else "draft",
    )
    session.add(lesson)
    await session.flush()

    for i, b in enumerate(blocks, start=1):
        session.add(LessonBlock(
            lesson_id=lesson.id,
            type=b["type"],
            position=i,
            text=b.get("text"),
            image_url=b.get("image_url"),
            caption=b.get("caption"),
        ))
    return lesson

async def grant_access_to_users(session: AsyncSession, *, lesson_id: int, user_ids: Sequence[int]) -> int:
    if not user_ids:
        return 0
    for uid in user_ids:
        session.merge(LessonAccessUser(lesson_id=lesson_id, user_id=uid))
    return len(user_ids)

async def grant_access_to_groups(session: AsyncSession, *, lesson_id: int, group_ids: Sequence[int]) -> int:
    if not group_ids:
        return 0
    res = await session.execute(select(Group.id).where(Group.id.in_(group_ids)))
    valid_ids = [gid for (gid,) in res.all()]
    for gid in valid_ids:
        session.merge(LessonAccessGroup(lesson_id=lesson_id, group_id=gid))
    return len(valid_ids)

async def get_accessible_lessons_for_user(session: AsyncSession, *, user_id: int) -> list["Lesson"]:
    user_groups = select(GroupMember.group_id).where(GroupMember.user_id == user_id)

    user_access_exists = select(literal(1)).where(
        and_(
            LessonAccessUser.lesson_id == Lesson.id,
            LessonAccessUser.user_id == user_id,
            or_(LessonAccessUser.expires_at.is_(None), LessonAccessUser.expires_at > func.datetime("now")),
        )
    ).exists()

    group_access_exists = select(literal(1)).where(
        and_(
            LessonAccessGroup.lesson_id == Lesson.id,
            LessonAccessGroup.group_id.in_(user_groups),
        )
    ).exists()

    stmt = (
        select(Lesson)
        .where(Lesson.status == "published")
        .where(or_(user_access_exists, group_access_exists))
        .order_by(Lesson.id.desc())
    )
    res = await session.execute(stmt)
    return res.scalars().all()

async def get_lesson_with_blocks(session: AsyncSession, lesson_id: int) -> "Lesson | None":
    return await session.get(Lesson, lesson_id)
