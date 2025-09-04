from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import User, Task


# ---------- Pydantic DTO ----------
class TaskSchema(BaseModel):
    id: int
    title: str
    completed: bool
    user_id: int

    model_config = ConfigDict(from_attributes=True)


# ---------- Repo functions ----------
async def add_user(session: AsyncSession, tg_id: int) -> User:
    """
    Ensure user by Telegram ID. Create if not exists.
    """
    user = await session.scalar(select(User).where(User.telegram_id == tg_id))
    if user:
        return user

    user = User(telegram_id=tg_id, is_active=True)
    session.add(user)
    await session.flush()  # получим user.id без commit
    return user


async def get_tasks(session: AsyncSession, user_id: int) -> List[dict]:
    """
    Return all NOT completed tasks for the user.
    """
    result = await session.scalars(
        select(Task).where(Task.user_id == user_id, Task.completed.is_(False))
    )
    tasks = result.all()
    return [TaskSchema.model_validate(t).model_dump() for t in tasks]


async def get_completed_tasks_count(session: AsyncSession, user_id: int) -> int:
    """
    Count completed tasks for the user.
    """
    count = await session.scalar(
        select(func.count(Task.id)).where(Task.user_id == user_id, Task.completed.is_(True))
    )
    return int(count or 0)


async def add_task(session: AsyncSession, user_id: int, title: str) -> Task:
    """
    Create a new task for user.
    """
    task = Task(title=title, user_id=user_id, completed=False)
    session.add(task)
    await session.flush()
    return task


async def update_task(session: AsyncSession, task_id: int) -> bool:
    """
    Mark a task as completed. Returns True if something was updated.
    """
    res = await session.execute(
        update(Task)
        .where(Task.id == task_id, Task.completed.is_(False))
        .values(completed=True)
    )
    return res.rowcount > 0
