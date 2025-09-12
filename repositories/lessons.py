from typing import Sequence, Optional
from sqlalchemy import select, func, and_, or_, literal, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Lesson, LessonBlock, Subject,
    LessonAccessUser, LessonAccessGroup,
    Group, GroupMember, SubjectAccessGroup, User
)

class SubjectNotFoundError(Exception): ...
class PayloadInvalidError(Exception): ...

# --- Создание ---------------------------------------------------------------

async def create_lesson(
    session: AsyncSession,
    *,
    subject_id: int,
    title: str,
    blocks: Sequence[dict],
    publish: bool = True,
    publish_at = None,  # datetime | None
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
        publish_at=publish_at,
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

# --- Детали/обновление ------------------------------------------------------

async def get_lesson_detail(session: AsyncSession, lesson_id: int) -> tuple["Lesson", list[int]] | None:
    lesson = await session.get(Lesson, lesson_id)
    if not lesson:
        return None
    rows = await session.execute(
        select(LessonAccessGroup.group_id).where(LessonAccessGroup.lesson_id == lesson_id)
    )
    group_ids = [gid for (gid,) in rows.all()]
    # блоки уже подгружаются через relationship(order_by position) с lazy="selectin" в модели
    return lesson, group_ids

async def replace_lesson_blocks(session: AsyncSession, *, lesson_id: int, blocks: Sequence[dict]) -> int:
    await session.execute(delete(LessonBlock).where(LessonBlock.lesson_id == lesson_id))
    n = 0
    for i, b in enumerate(blocks, start=1):
        session.add(LessonBlock(
            lesson_id=lesson_id,
            type=b["type"],
            position=i,
            text=b.get("text"),
            image_url=b.get("image_url"),
            caption=b.get("caption"),
        ))
        n += 1
    return n

async def set_lesson_groups(session: AsyncSession, *, lesson_id: int, group_ids: Sequence[int]) -> int:
    await session.execute(delete(LessonAccessGroup).where(LessonAccessGroup.lesson_id == lesson_id))
    if not group_ids:
        return 0
    valid = await session.execute(select(Group.id).where(Group.id.in_(group_ids)))
    ids = [gid for (gid,) in valid.all()]
    for gid in ids:
        session.merge(LessonAccessGroup(lesson_id=lesson_id, group_id=gid))
    return len(ids)

async def update_lesson(
    session: AsyncSession,
    *,
    lesson_id: int,
    title: Optional[str] = None,
    publish: Optional[bool] = None,
    publish_at = None,   # datetime | None
    blocks: Optional[Sequence[dict]] = None,
    group_ids: Optional[Sequence[int]] = None,
    user_ids: Optional[Sequence[int]] = None,
) -> "Lesson | None":
    lesson = await session.get(Lesson, lesson_id)
    if not lesson:
        return None
    changed = False
    if title is not None and lesson.title != title:
        lesson.title = title; changed = True
    if publish is not None:
        lesson.status = "published" if publish else "draft"; changed = True
    if publish_at is not None:
        lesson.publish_at = publish_at; changed = True
    if blocks is not None:
        # простая стратегия: полный replace
        await replace_lesson_blocks(session, lesson_id=lesson_id, blocks=blocks)
    if group_ids is not None:
        await set_lesson_groups(session, lesson_id=lesson_id, group_ids=group_ids)
    if user_ids:
        for uid in user_ids:
            session.merge(LessonAccessUser(lesson_id=lesson_id, user_id=uid))
    if changed:
        await session.flush()
    return lesson

# --- Доступность уроков -----------------------------------------------------

async def get_accessible_lessons_for_user(session: AsyncSession, *, user_id: int) -> list["Lesson"]:
    user_groups = select(GroupMember.group_id).where(GroupMember.user_id == user_id)


async def list_subject_lessons_with_group_ids(session: AsyncSession, subject_id: int) -> list[tuple[Lesson, list[int]]]:
    """Вернёт [(Lesson, [group_ids]), ...] по subject_id, сортировка id DESC."""
    lessons = (await session.execute(
        select(Lesson).where(Lesson.subject_id == subject_id).order_by(Lesson.id.desc())
    )).scalars().all()
    if not lessons:
        return []

    ids = [l.id for l in lessons]
    rows = await session.execute(
        select(LessonAccessGroup.lesson_id, LessonAccessGroup.group_id)
        .where(LessonAccessGroup.lesson_id.in_(ids))
    )
    mapping: dict[int, list[int]] = {}
    for lid, gid in rows.all():
        mapping.setdefault(lid, []).append(gid)

    return [(l, mapping.get(l.id, [])) for l in lessons]
