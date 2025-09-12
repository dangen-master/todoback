from typing import List, Sequence, Optional
from sqlalchemy import select, delete, insert
from sqlalchemy.ext.asyncio import AsyncSession
from models import Subject, SubjectAccessGroup, Group, Lesson

# --- CRUD предметов ---------------------------------------------------------

async def create_subject(
    session: AsyncSession,
    *,
    name: str,
    description: str | None = None,
) -> Subject:
    subj = Subject(name=name, description=description)
    session.add(subj)
    await session.flush()
    return subj

async def list_subjects_with_group_ids(session: AsyncSession) -> list[tuple[Subject, list[int]]]:
    """Вернёт [(Subject, [group_ids])...]"""
    subjects = (await session.execute(select(Subject))).scalars().all()
    res: list[tuple[Subject, list[int]]] = []
    for s in subjects:
        gids = await session.execute(
            select(SubjectAccessGroup.group_id).where(SubjectAccessGroup.subject_id == s.id)
        )
        res.append((s, [gid for (gid,) in gids.all()]))
    return res

async def get_subject_with_group_ids(session: AsyncSession, subject_id: int) -> tuple[Subject, list[int]] | None:
    subj = await session.get(Subject, subject_id)
    if not subj:
        return None
    gids = await session.execute(
        select(SubjectAccessGroup.group_id).where(SubjectAccessGroup.subject_id == subject_id)
    )
    return subj, [gid for (gid,) in gids.all()]

async def set_subject_groups(session, subject_id: int, group_ids: list[int]) -> None:
    ids = {int(g) for g in (group_ids or [])}
    if ids:
        rows = await session.execute(select(Group.id).where(Group.id.in_(ids)))
        ids = {r[0] for r in rows.all()}  # только существующие группы

    await session.execute(delete(SubjectAccessGroup).where(SubjectAccessGroup.subject_id == subject_id))
    if ids:
        await session.execute(
            insert(SubjectAccessGroup),
            [{"subject_id": subject_id, "group_id": gid} for gid in ids]
        )

async def update_subject(
    session: AsyncSession,
    *,
    subject_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    group_ids: Optional[Sequence[int]] = None,
) -> Subject | None:
    subj = await session.get(Subject, subject_id)
    if not subj:
        return None
    changed = False
    if name is not None and subj.name != name:
        subj.name = name; changed = True
    if description is not None and subj.description != description:
        subj.description = description; changed = True
    if group_ids is not None:
        await set_subject_groups(session, subject_id=subject_id, group_ids=group_ids)
    if changed:
        await session.flush()
    return subj

async def get_subject_group_ids(session, subject_id: int) -> list[int]:
    rows = await session.execute(
        select(SubjectAccessGroup.group_id).where(SubjectAccessGroup.subject_id == subject_id)
    )
    return [r[0] for r in rows.all()]

# --- Выборки уроков по предмету --------------------------------------------

async def list_subject_lessons(session: AsyncSession, subject_id: int) -> list[Lesson]:
    rows = await session.execute(
        select(Lesson).where(Lesson.subject_id == subject_id).order_by(Lesson.id.desc())
    )
    return rows.scalars().all()

