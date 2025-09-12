from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime, Enum,
    ForeignKey, UniqueConstraint, Index, CheckConstraint, func, text, event, select
)
from sqlalchemy import BigInteger as SA_BigInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

# ---------- Async engine (SQLite dev) ----------
engine = create_async_engine("sqlite+aiosqlite:///db.sqlite3", echo=True)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False)

@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()

class Base(DeclarativeBase):
    pass

# ---------- Enums ----------
LessonStatusEnum = Enum(
    "draft", "published",
    name="lesson_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
BlockTypeEnum = Enum(
    "text", "image",
    name="block_type",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)

PK_INT = Integer
FK_INT = Integer

# ---------- Mixins ----------
class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.datetime("now"), nullable=False
    )

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.datetime("now"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.datetime("now"),
        onupdate=func.datetime("now"), nullable=False
    )

# ---------- Models ----------
class User(CreatedAtMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(SA_BigInteger, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    avatar_url: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))

    roles: Mapped[List["Role"]] = relationship("Role", secondary="user_roles", back_populates="users", lazy="selectin")
    groups: Mapped[List["Group"]] = relationship("Group", secondary="group_members", back_populates="members", lazy="selectin")

    def __repr__(self) -> str:
        return f"<User id={self.id} tg={self.telegram_id}>"

class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    users: Mapped[List["User"]] = relationship("User", secondary="user_roles", back_populates="roles")

class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)

class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    members: Mapped[List["User"]] = relationship("User", secondary="group_members", back_populates="groups", lazy="selectin")
    lessons: Mapped[List["Lesson"]] = relationship("Lesson", back_populates="group", lazy="selectin")
    lesson_access: Mapped[List["LessonAccessGroup"]] = relationship("LessonAccessGroup", back_populates="group")
    subject_access: Mapped[List["SubjectAccessGroup"]] = relationship("SubjectAccessGroup", back_populates="group")

class GroupMember(Base):
    __tablename__ = "group_members"
    group_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    __table_args__ = (Index("idx_group_members_user", "user_id"),)

class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    lessons: Mapped[List["Lesson"]] = relationship("Lesson", back_populates="subject", cascade="all, delete-orphan", lazy="selectin")
    access_groups: Mapped[List["SubjectAccessGroup"]] = relationship("SubjectAccessGroup", back_populates="subject", cascade="all, delete-orphan")

class SubjectAccessGroup(Base):
    __tablename__ = "subject_access_groups"
    subject_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)

    subject: Mapped["Subject"] = relationship("Subject", back_populates="access_groups")
    group: Mapped["Group"] = relationship("Group", back_populates="subject_access")

class Lesson(Base):
    __tablename__ = "lessons"
    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    subject_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(LessonStatusEnum, nullable=False, server_default=text("'draft'"))
    publish_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))  # доступно с этой даты/времени

    group_id: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("groups.id", ondelete="SET NULL"))
    group: Mapped[Optional["Group"]] = relationship("Group", back_populates="lessons")

    subject: Mapped["Subject"] = relationship("Subject", back_populates="lessons")
    blocks: Mapped[List["LessonBlock"]] = relationship(
        "LessonBlock", back_populates="lesson", cascade="all, delete-orphan",
        order_by="LessonBlock.position", lazy="selectin",
    )
    access_users: Mapped[List["LessonAccessUser"]] = relationship("LessonAccessUser", back_populates="lesson", cascade="all, delete-orphan")
    access_groups: Mapped[List["LessonAccessGroup"]] = relationship("LessonAccessGroup", back_populates="lesson", cascade="all, delete-orphan")
    views: Mapped[List["LessonView"]] = relationship("LessonView", back_populates="lesson")
    bookmarks: Mapped[List["Bookmark"]] = relationship("Bookmark", back_populates="lesson")

    __table_args__ = (
        Index("idx_lessons_subject", "subject_id"),
        Index("idx_lessons_group", "group_id"),
        Index("idx_lessons_publish_at", "publish_at"),
    )

class LessonBlock(TimestampMixin, Base):
    __tablename__ = "lesson_blocks"
    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(BlockTypeEnum, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    caption: Mapped[Optional[str]] = mapped_column(Text)
    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="blocks")

    __table_args__ = (
        UniqueConstraint("lesson_id", "position", name="uq_lesson_block_position"),
        Index("idx_lesson_blocks_lesson", "lesson_id", "position"),
        CheckConstraint("(type <> 'text') OR (text IS NOT NULL)", name="chk_block_text_when_text"),
        CheckConstraint("(type <> 'image') OR (image_url IS NOT NULL)", name="chk_block_image_when_image"),
    )

class LessonAccessUser(Base):
    __tablename__ = "lesson_access_users"
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="access_users")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_access_users_user", "user_id"),
        Index("idx_access_users_lesson", "lesson_id"),
    )

class LessonAccessGroup(Base):
    __tablename__ = "lesson_access_groups"
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="access_groups")
    group: Mapped["Group"] = relationship("Group", back_populates="lesson_access")

    __table_args__ = (
        Index("idx_access_groups_group", "group_id"),
        Index("idx_access_groups_lesson", "lesson_id"),
    )

class LessonView(Base):
    __tablename__ = "lesson_views"
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.datetime("now"), nullable=False)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="views")
    user: Mapped["User"] = relationship("User")

class Bookmark(Base):
    __tablename__ = "bookmarks"
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.datetime("now"), nullable=False)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="bookmarks")
    user: Mapped["User"] = relationship("User")

# ---------- Schema init / seed ----------
async def seed_initial_data(session: AsyncSession) -> None:
    # 1) роли (понадобятся для админки, пользователей НЕ создаём)
    for code, name in [("student", "Student"), ("teacher", "Teacher"), ("admin", "Admin")]:
        if not await session.scalar(select(Role).where(Role.code == code)):
            session.add(Role(code=code, name=name))
    await session.flush()

    # 2) группы
    group_names = ["Группа 11-ИС", "Frontend-курс", "Бэкенд-курс"]
    groups: dict[str, Group] = {}
    for n in group_names:
        g = await session.scalar(select(Group).where(Group.name == n))
        if not g:
            g = Group(name=n)
            session.add(g)
            await session.flush()
        groups[n] = g

    # 3) предметы + доступ по предметам
    subjects_data = [
        {"name": "Алгебра",      "description": "Базовый курс алгебры",               "group_names": ["Группа 11-ИС"]},
        {"name": "Геометрия",    "description": "Фигуры, теоремы и доказательства",    "group_names": ["Группа 11-ИС"]},
        {"name": "Физика",       "description": "Механика, оптика, электричество",     "group_names": ["Frontend-курс"]},
        {"name": "Информатика",  "description": "Алгоритмы и структуры данных",        "group_names": ["Frontend-курс", "Бэкенд-курс"]},
        {"name": "История",      "description": "Мировая история XX века",             "group_names": []},  # доступ раздаём на уровне уроков
    ]

    subjects: dict[str, Subject] = {}
    for s in subjects_data:
        subj = await session.scalar(select(Subject).where(Subject.name == s["name"]))
        if not subj:
            subj = Subject(name=s["name"], description=s["description"])
            session.add(subj)
            await session.flush()
        # subject_access_groups (upsert через merge)
        for gn in s["group_names"]:
            session.merge(SubjectAccessGroup(subject_id=subj.id, group_id=groups[gn].id))
        subjects[s["name"]] = subj

    await session.flush()

    # 4) уроки по предметам
    lessons_data = {
        "Алгебра": [
            {"title": "Урок 1. Множества",                 "status": "published", "publish_at": func.datetime("now"),             "grant_groups": []},
            {"title": "Урок 2. Операции над множествами",  "status": "published", "publish_at": func.datetime("now", "+1 day"),   "grant_groups": []},
            {"title": "Урок 3. Последовательности",        "status": "draft",     "publish_at": None,                             "grant_groups": []},
        ],
        "Геометрия": [
            {"title": "Треугольники и их свойства",        "status": "published", "publish_at": func.datetime("now"),             "grant_groups": []},
            {"title": "Параллельные прямые",               "status": "published", "publish_at": func.datetime("now", "+2 days"),  "grant_groups": []},
            {"title": "Площадь фигур",                     "status": "draft",     "publish_at": None,                             "grant_groups": []},
        ],
        "Физика": [
            {"title": "Кинематика: равномерное движение",  "status": "published", "publish_at": func.datetime("now"),             "grant_groups": []},
            {"title": "Динамика: законы Ньютона",          "status": "published", "publish_at": func.datetime("now", "+1 day"),   "grant_groups": []},
            {"title": "Импульс и энергия",                 "status": "draft",     "publish_at": None,                             "grant_groups": []},
        ],
        "Информатика": [
            {"title": "Сложность алгоритмов (Big-O)",      "status": "published", "publish_at": func.datetime("now"),             "grant_groups": ["Бэкенд-курс"]},
            {"title": "Сортировки: быстрая и слиянием",    "status": "published", "publish_at": func.datetime("now"),             "grant_groups": []},
            {"title": "Структуры данных: дерево/граф",     "status": "published", "publish_at": func.datetime("now", "+3 days"),  "grant_groups": []},
        ],
        "История": [
            {"title": "Первая мировая война",              "status": "published", "publish_at": func.datetime("now"),             "grant_groups": ["Группа 11-ИС"]},
            {"title": "Вторая мировая война",              "status": "published", "publish_at": func.datetime("now"),             "grant_groups": ["Frontend-курс"]},
            {"title": "Холодная война",                    "status": "published", "publish_at": func.datetime("now", "+1 day"),   "grant_groups": ["Группа 11-ИС", "Frontend-курс"]},
        ],
    }

    for subj_name, lessons in lessons_data.items():
        subj = subjects[subj_name]
        for item in lessons:
            # проверка на существование урока по (subject_id, title)
            exists_id = await session.scalar(
                select(Lesson.id).where(Lesson.subject_id == subj.id, Lesson.title == item["title"])
            )
            if exists_id:
                continue

            lesson = Lesson(
                subject_id=subj.id,
                title=item["title"],
                status=item["status"],
                publish_at=item["publish_at"],
                # можно также задавать lesson.group_id, но здесь используем выдачу через access-группы
            )
            session.add(lesson)
            await session.flush()

            # базовые блоки контента
            session.add_all([
                LessonBlock(lesson_id=lesson.id, type="text",  position=1, text=f"{item['title']}: вводная часть."),
                LessonBlock(lesson_id=lesson.id, type="image", position=2, image_url="https://placehold.co/800x400", caption="Иллюстрация к теме"),
            ])

            # точечные выдачи доступа к уроку (поверх доступа по предмету)
            for gn in item.get("grant_groups", []):
                session.merge(LessonAccessGroup(lesson_id=lesson.id, group_id=groups[gn].id))

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        await seed_initial_data(session)
        await session.commit()
