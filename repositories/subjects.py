from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Subject


async def create_subject(
    session: AsyncSession,
    *,
    name: str,
    code: str | None = None,
    description: str | None = None,
    created_by: int | None = None,
) -> Subject:
    subj = Subject(name=name, code=code, description=description, created_by=created_by)
    session.add(subj)
    await session.flush()
    return subj


async def list_subjects(session: AsyncSession) -> List[Subject]:
    res = await session.execute(select(Subject).order_by(Subject.name.asc()))
    return res.scalars().all()
