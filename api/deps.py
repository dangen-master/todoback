from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from models import async_session
from repositories import users as users_repo

async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session

async def get_current_user(
    session: AsyncSession = Depends(get_session),
    x_debug_tg_id: Optional[int] = Header(None, alias="X-Debug-Tg-Id"),
):
    if not x_debug_tg_id:
        raise HTTPException(status_code=401, detail="Missing X-Debug-Tg-Id")
    user = await users_repo.get_user_by_tg(session, x_debug_tg_id)
    if not user:
        user = await users_repo.ensure_user(session, x_debug_tg_id)
        await session.commit()
    return user

def require_roles(*allowed: str):
    async def checker(
        me = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ):
        prof = await users_repo.get_user_profile(session, me.telegram_id)
        codes = {r.code for r in (prof.roles if prof else [])}
        if not (set(allowed) & codes):
            raise HTTPException(status_code=403, detail="Forbidden")
        return prof or me
    return checker

async def can_view_lesson(session: AsyncSession, tg_id: Optional[int], lesson_group_ids: list[int]) -> bool:
    if not lesson_group_ids:
        return True
    if not tg_id:
        return False
    prof = await users_repo.get_user_profile(session, tg_id)
    if not prof:
        return False
    codes = {r.code for r in prof.roles}
    if "admin" in codes or "teacher" in codes:
        return True
    my_groups = {g.id for g in prof.groups}
    return any(g in my_groups for g in lesson_group_ids)
