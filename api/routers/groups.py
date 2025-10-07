from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, require_roles
from api.schemas import GroupCreateIn, GroupPatchIn, GroupMemberIn
from models import Group
from repositories import users as users_repo

router = APIRouter(tags=["groups"])

@router.get("/groups", dependencies=[Depends(require_roles("admin","teacher"))])
async def list_groups(session: AsyncSession = Depends(get_session)):
    groups = await users_repo.list_groups_with_members(session)
    return [{"id": g.id, "name": g.name,
             "members": [{"tg_id": u.telegram_id, "first_name": u.first_name, "last_name": u.last_name,
                          "username": u.username, "avatar_url": u.avatar_url} for u in g.members]}
            for g in groups]

@router.post("/groups", dependencies=[Depends(require_roles("admin","teacher"))], status_code=201)
async def create_group(body: GroupCreateIn, session: AsyncSession = Depends(get_session)):
    g = await users_repo.create_group(session, name=body.name)
    await session.commit()
    return {"id": g.id, "name": g.name}

@router.patch("/groups/{group_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def patch_group(group_id: int, body: GroupPatchIn, session: AsyncSession = Depends(get_session)):
    g = await session.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    g.name = body.name
    await session.flush()
    await session.commit()
    return {"id": g.id, "name": g.name}

@router.post("/groups/{group_id}/members", dependencies=[Depends(require_roles("admin","teacher"))])
async def add_group_member(group_id: int, body: GroupMemberIn, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.add_user_to_group(session, body.tg_id, group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User or group not found")
    await session.commit()
    return {"status": "ok"}

@router.delete("/groups/{group_id}/members/{tg_id}", dependencies=[Depends(require_roles("admin","teacher"))])
async def remove_group_member(group_id: int, tg_id: int, session: AsyncSession = Depends(get_session)):
    ok = await users_repo.remove_user_from_group(session, tg_id, group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User or group not found")
    await session.commit()
    return {"status": "ok"}

@router.delete("/groups/{group_id}", dependencies=[Depends(require_roles("admin","teacher"))], status_code=204)
async def delete_group(group_id: int, session: AsyncSession = Depends(get_session)):
    g = await session.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    await session.delete(g)
    await session.commit()
    return None
