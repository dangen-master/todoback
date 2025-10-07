from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, require_roles
from api.schemas import EnsureUserIn, UserProfileOut
from repositories import users as users_repo

router = APIRouter(tags=["users"])

@router.post("/user/ensure", response_model=UserProfileOut)
async def api_ensure_user(data: EnsureUserIn, session: AsyncSession = Depends(get_session)):
    await users_repo.ensure_user(
        session, data.tg_id,
        username=data.username, first_name=data.first_name,
        last_name=data.last_name, avatar_url=data.avatar_url,
    )
    await session.commit()
    prof = await users_repo.get_user_profile(session, data.tg_id)
    return UserProfileOut(
        tg_id=prof.telegram_id, username=prof.username, first_name=prof.first_name,
        last_name=prof.last_name, avatar_url=prof.avatar_url,
        roles=[r.code for r in prof.roles],
        groups=[{"id": g.id, "name": g.name} for g in prof.groups],
    )

@router.get("/user/profile/{tg_id}", response_model=UserProfileOut)
async def get_user_profile(tg_id: int, session: AsyncSession = Depends(get_session)):
    user = await users_repo.get_user_profile(session, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfileOut(
        tg_id=user.telegram_id, username=user.username, first_name=user.first_name,
        last_name=user.last_name, avatar_url=user.avatar_url,
        roles=[r.code for r in user.roles],
        groups=[{"id": g.id, "name": g.name} for g in user.groups],
    )

@router.get("/users", dependencies=[Depends(require_roles("admin","teacher"))])
async def list_users(session: AsyncSession = Depends(get_session)):
    users = await users_repo.list_users_with_details(session)
    return [
        {
            "tg_id": u.telegram_id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "avatar_url": u.avatar_url,
            "roles": [r.code for r in u.roles],
            "groups": [{"id": g.id, "name": g.name} for g in u.groups],
        } for u in users
    ]
