from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import User


async def ensure_user(
    session: AsyncSession,
    tg_id: int,
    *,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> User:
    """Find user by telegram_id or create a new one.
    Updates profile fields if provided."""
    user = await session.scalar(select(User).where(User.telegram_id == tg_id))
    if user:
        changed = False
        if username is not None and user.username != username:
            user.username = username; changed = True
        if first_name is not None and user.first_name != first_name:
            user.first_name = first_name; changed = True
        if last_name is not None and user.last_name != last_name:
            user.last_name = last_name; changed = True
        if avatar_url is not None and user.avatar_url != avatar_url:
            user.avatar_url = avatar_url; changed = True
        if changed:
            await session.flush()
        return user

    user = User(
        telegram_id=tg_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        avatar_url=avatar_url,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def get_user_by_tg(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == tg_id))

async def list_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.id))
    return result.scalars().all()

async def get_user_profile(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(
        select(User)
        .options(
            selectinload(User.roles),
            selectinload(User.groups),
        )
        .where(User.telegram_id == tg_id)
    )