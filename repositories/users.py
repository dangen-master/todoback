from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import User, Role, UserRole, Group, GroupMember

# --- helpers ---------------------------------------------------------------
async def _get_or_create_role(session: AsyncSession, code: str, name: Optional[str] = None) -> Role:
    role = await session.scalar(select(Role).where(Role.code == code))
    if role:
        return role
    role = Role(code=code, name=(name or code.capitalize()))
    session.add(role)
    await session.flush()
    return role

async def _ensure_student_role(session, user: User) -> None:
    # 1) найдём/создадим саму роль
    role = await session.scalar(select(Role).where(Role.code == "student"))
    if role is None:
        role = Role(code="student", name="Студент")
        session.add(role)
        # flush гарантирует role.id
        await session.flush()

    # 2) проверим членство БЕЗ обращения к user.roles (без lazy-load)
    #    через явный запрос с join по связи User.roles
    is_member = await session.scalar(
        select(Role.id)
        .join(User.roles)
        .where(User.id == user.id, Role.id == role.id)
        .limit(1)
    )

    # 3) если ещё не состоит — добавим и зафлашим
    if is_member is None:
        # это безопасно: без ленивой загрузки; append сработает
        user.roles.append(role)
        await session.flush()

# --- public api ------------------------------------------------------------
async def ensure_user(session: AsyncSession, tg_id: int, *, username: Optional[str] = None,
                      first_name: Optional[str] = None, last_name: Optional[str] = None,
                      avatar_url: Optional[str] = None) -> User:
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
        await _ensure_student_role(session, user)
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
    await _ensure_student_role(session, user)
    return user

async def get_user_by_tg(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == tg_id))

async def list_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.id))
    return result.scalars().all()

class GroupAlreadyExistsError(Exception): ...
async def get_user_profile(session, tg_id: int) -> User | None:
    return await session.scalar(
        select(User)
        .options(selectinload(User.roles), selectinload(User.groups))
        .where(User.telegram_id == tg_id)
    )

async def add_role_to_user(session: AsyncSession, tg_id: int, role_code: str) -> bool:
    user = await session.scalar(select(User).where(User.telegram_id == tg_id))
    if not user:
        return False
    role = await session.scalar(select(Role).where(Role.code == role_code))
    if not role:
        role = Role(code=role_code, name=role_code.capitalize())
        session.add(role)
        await session.flush()
    exists = await session.scalar(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id))
    if exists:
        return True
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return True

async def add_user_to_group(session: AsyncSession, tg_id: int, group_id: int) -> bool:
    user = await session.scalar(select(User).where(User.telegram_id == tg_id))
    if not user:
        return False
    group = await session.get(Group, group_id)
    if not group:
        return False
    exists = await session.scalar(select(GroupMember).where(GroupMember.user_id == user.id, GroupMember.group_id == group_id))
    if exists:
        return True
    session.add(GroupMember(user_id=user.id, group_id=group_id))
    return True

async def remove_role_from_user(session: AsyncSession, tg_id: int, role_code: str) -> bool:
    user = await session.scalar(select(User).where(User.telegram_id == tg_id))
    if not user:
        return False
    role = await session.scalar(select(Role).where(Role.code == role_code))
    if not role:
        return True
    await session.execute(delete(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id))
    return True

async def remove_user_from_group(session: AsyncSession, tg_id: int, group_id: int) -> bool:
    user = await session.scalar(select(User).where(User.telegram_id == tg_id))
    if not user:
        return False
    await session.execute(delete(GroupMember).where(GroupMember.user_id == user.id, GroupMember.group_id == group_id))
    return True

async def list_users_with_details(session: AsyncSession) -> list[User]:
    res = await session.execute(
        select(User).options(selectinload(User.roles), selectinload(User.groups)).order_by(User.id.asc())
    )
    return res.scalars().all()

async def list_roles_with_members(session: AsyncSession) -> list[tuple[Role, list[User]]]:
    roles = (await session.execute(select(Role).order_by(Role.code.asc()))).scalars().all()
    result: list[tuple[Role, list[User]]] = []
    for r in roles:
        users = await session.execute(
            select(User)
            .join(UserRole, UserRole.user_id == User.id)
            .where(UserRole.role_id == r.id)
            .order_by(User.first_name.asc(), User.last_name.asc(), User.username.asc())
        )
        result.append((r, users.scalars().all()))
    return result

async def list_groups_with_members(session: AsyncSession) -> list[Group]:
    res = await session.execute(select(Group).options(selectinload(Group.members)).order_by(Group.name.asc()))
    return res.scalars().all()

async def create_group(session: AsyncSession, *, name: str) -> Group:
    exists = await session.scalar(select(Group).where(Group.name == name))
    if exists:
        raise GroupAlreadyExistsError("Group already exists")
    g = Group(name=name)
    session.add(g)
    await session.flush()
    return g
