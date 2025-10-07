from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from api.deps import get_session, require_roles
from api.schemas import RoleMemberIn
from repositories import users as users_repo

router = APIRouter(tags=["roles"])

@router.get("/roles", dependencies=[Depends(require_roles("admin","teacher"))])
async def get_roles(session: AsyncSession = Depends(get_session)):
    rows = await users_repo.list_roles_with_members(session)
    return [
        {"key": role.code, "title": role.name,
         "members": [{"tg_id": u.telegram_id, "first_name": u.first_name, "last_name": u.last_name,
                      "username": u.username, "avatar_url": u.avatar_url} for u in members]}
        for role, members in rows
    ]

@router.post("/roles/{role}/members", dependencies=[Depends(require_roles("admin","teacher"))])
async def add_role_member(role: str, body: RoleMemberIn, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.add_role_to_user(session, body.tg_id, role)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    await session.commit()
    return {"status": "ok"}

@router.delete("/roles/{role}/members/{tg_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def remove_role_member(role: str, tg_id: int, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.remove_role_from_user(session, tg_id, role)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    await session.commit()
    return {"status": "ok"}
