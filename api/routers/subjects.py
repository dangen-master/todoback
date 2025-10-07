from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, require_roles
from api.schemas import (
    SubjectOut, SubjectOutFull, SubjectCreateIn, SubjectPatchIn, LessonListItemOut
)
from repositories import subjects as subjects_repo
from repositories import lessons as lessons_repo

router = APIRouter(tags=["subjects"])

@router.get("/subjects", response_model=list[SubjectOut])
async def list_subjects(session: AsyncSession = Depends(get_session)):
    rows = await subjects_repo.list_subjects_with_group_ids(session)
    return [SubjectOut(id=s.id, name=s.name, description=s.description, group_ids=gids) for (s, gids) in rows]

@router.get("/subjects/{subject_id}", response_model=SubjectOutFull)
async def get_subject(subject_id: int, session: AsyncSession = Depends(get_session)):
    row = await subjects_repo.get_subject_with_group_ids(session, subject_id)
    if not row:
        raise HTTPException(status_code=404, detail="Subject not found")
    s, gids = row
    return SubjectOutFull(id=s.id, name=s.name, description=s.description, group_ids=gids)

@router.post("/subjects", response_model=SubjectOut, status_code=201, dependencies=[Depends(require_roles("admin","teacher"))])
async def create_subject(payload: SubjectCreateIn, session: AsyncSession = Depends(get_session)):
    subj = await subjects_repo.create_subject(session, name=payload.name, description=payload.description)
    await session.flush()
    if payload.group_ids is not None:
        await subjects_repo.set_subject_groups(session, subject_id=subj.id, group_ids=payload.group_ids)
    await session.commit()
    s, gids = await subjects_repo.get_subject_with_group_ids(session, subj.id)
    return SubjectOut(id=s.id, name=s.name, description=s.description, group_ids=gids)

@router.patch("/subjects/{subject_id}", response_model=SubjectOutFull, dependencies=[Depends(require_roles("admin","teacher"))])
async def patch_subject(subject_id: int, body: SubjectPatchIn, session: AsyncSession = Depends(get_session)):
    subj = await subjects_repo.update_subject(session, subject_id=subject_id, name=body.name, description=body.description, group_ids=body.group_ids)
    if not subj:
        raise HTTPException(status_code=404, detail="Subject not found")
    await session.commit()
    s, gids = await subjects_repo.get_subject_with_group_ids(session, subject_id)
    return SubjectOutFull(id=s.id, name=s.name, description=s.description, group_ids=gids)

@router.get("/subjects/{subject_id}/lessons", response_model=list[LessonListItemOut], dependencies=[Depends(require_roles("admin","teacher"))])
async def subject_lessons(subject_id: int, session: AsyncSession = Depends(get_session)):
    rows = await lessons_repo.list_subject_lessons_with_group_ids(session, subject_id)
    return [
        LessonListItemOut(id=l.id, subject_id=l.subject_id, title=l.title, status=l.status, publish_at=l.publish_at, group_ids=gids)
        for (l, gids) in rows
    ]

@router.delete("/subjects/{subject_id}", status_code=204, dependencies=[Depends(require_roles("admin","teacher"))])
async def delete_subject_api(subject_id: int, session: AsyncSession = Depends(get_session)):
    await lessons_repo.delete_lessons_by_subject(session, subject_id)
    ok = await subjects_repo.delete_subject(session, subject_id)
    if not ok:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Subject not found")
    await session.commit()
    return None
