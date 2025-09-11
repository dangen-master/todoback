from typing import List, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Subject, SubjectAccessGroup, Group

async def create_subject(session: AsyncSession, *, name: str, description: str | None = None) -> Subject:
    subj = Subject(name=name, description=description)
    session.add(subj)
    await session.flush()
    return subj

async def list_subjects(session: AsyncSession) -> List[Subject]:
    res = await session.execute(select(Subject).order_by(Subject.name.asc()))
    return res.scalars().all()

async def grant_subject_to_groups(session: AsyncSession, *, subject_id: int, group_ids: Sequence[int]) -> int:
    if not group_ids:
        return 0
    res = await session.execute(select(Group.id).where(Group.id.in_(group_ids)))
    valid = [gid for (gid,) in res.all()]
    for gid in valid:
        session.merge(SubjectAccessGroup(subject_id=subject_id, group_id=gid))
    return len(valid)
