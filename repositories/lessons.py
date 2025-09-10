from typing import List, Sequence

from sqlalchemy import select, func, and_, or_, literal
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Lesson, LessonBlock, Subject,
    LessonAccessUser, LessonAccessGroup,
    Group, GroupMember,
)


async def create_lesson(
    session: AsyncSession,
    *,
    subject_id: int,
    title: str,
    blocks: Sequence[dict],
    publish: bool = True,
    created_by: int | None = None,
) -> Lesson:
    # проверим предмет
    subj = await session.get(Subject, subject_id)
    if not subj:
        raise ValueError("Subject not found")

    lesson = Lesson(
        subject_id=subject_id,
        title=title,
        status="published" if publish else "draft",
        published_at=func.now() if publish else None,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(lesson)
    await session.flush()  # получим lesson.id

    for i, b in enumerate(blocks, start=1):
        session.add(LessonBlock(
            lesson_id=lesson.id,
            type=b["type"],                  # 'text' | 'image'
            position=i,
            text_content=b.get("text"),
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
    # убедимся, что группы существуют (не обязательно, но полезно)
    res = await session.execute(select(Group.id).where(Group.id.in_(group_ids)))
    valid_ids = [gid for (gid,) in res.all()]
    for gid in valid_ids:
        session.merge(LessonAccessGroup(lesson_id=lesson_id, group_id=gid))
    return len(valid_ids)


async def get_accessible_lessons_for_user(session: AsyncSession, *, user_id: int) -> List[Lesson]:
    # группы пользователя
    user_groups = select(GroupMember.group_id).where(GroupMember.user_id == user_id)

    # доступ персонально
    user_access_exists = select(literal(1)).where(
        and_(
            LessonAccessUser.lesson_id == Lesson.id,
            LessonAccessUser.user_id == user_id,
            or_(LessonAccessUser.expires_at.is_(None), LessonAccessUser.expires_at > func.now()),
        )
    ).exists()

    # доступ через группы
    group_access_exists = select(literal(1)).where(
        and_(
            LessonAccessGroup.lesson_id == Lesson.id,
            LessonAccessGroup.group_id.in_(user_groups),
            or_(LessonAccessGroup.expires_at.is_(None), LessonAccessGroup.expires_at > func.now()),
        )
    ).exists()

    stmt = (
        select(Lesson)
        .where(Lesson.status == "published")
        .where(or_(user_access_exists, group_access_exists))
        .order_by(func.coalesce(Lesson.published_at, Lesson.created_at).desc())
    )
    res = await session.execute(stmt)
    return res.scalars().all()


async def get_lesson_with_blocks(session: AsyncSession, lesson_id: int) -> Lesson | None:
    # простая загрузка; блоки подхватятся из relationship(order_by position) при обращении
    return await session.get(Lesson, lesson_id)
