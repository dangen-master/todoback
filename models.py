from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Integer, SmallInteger, String, Text, Boolean, DateTime, Enum,
    ForeignKey, UniqueConstraint, Index, func, text
)
from sqlalchemy import BigInteger as SA_BigInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# 1) Async engine (SQLite dev). Для prod см. ниже.
engine = create_async_engine("sqlite+aiosqlite:///db.sqlite3", echo=True)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

# 2) Enums: совместимо со всеми СУБД
LessonStatusEnum = Enum(
    "draft", "published",
    name="lesson_status",
    native_enum=False,           # важно для SQLite
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

# 3) Универсальные типы PK: для SQLite используем Integer PK (rowid)
PK_INT = Integer     # можно сменить на SA_BigInteger при переходе на PG, но см. комментарий ниже
FK_INT = Integer     # чтобы не мешать SQLite; на PG это тоже ок

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)  # INTEGER PRIMARY KEY — автонумерация в SQLite
    telegram_id: Mapped[Optional[int]] = mapped_column(SA_BigInteger, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    avatar_url: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.datetime("now"),  # SQLite-friendly onupdate; в PG можно оставить func.now()
        nullable=False,
    )

    roles: Mapped[List["Role"]] = relationship(
        "Role", secondary="user_roles", back_populates="users", lazy="selectin"
    )
    groups: Mapped[List["Group"]] = relationship(
        "Group", secondary="group_members", back_populates="members", lazy="selectin"
    )
    created_subjects: Mapped[List["Subject"]] = relationship(
        back_populates="created_by_user", foreign_keys="Subject.created_by", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} tg={self.telegram_id}>"

class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    users: Mapped[List[User]] = relationship("User", secondary="user_roles", back_populates="roles")

class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)

class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"),
                                                 onupdate=func.datetime("now"), nullable=False)

    members: Mapped[List[User]] = relationship("User", secondary="group_members", back_populates="groups", lazy="selectin")
    lesson_access: Mapped[List["LessonAccessGroup"]] = relationship("LessonAccessGroup", back_populates="group")

class GroupMember(Base):
    __tablename__ = "group_members"
    group_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_in_group: Mapped[Optional[str]] = mapped_column(String)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    __table_args__ = (Index("idx_group_members_user", "user_id"),)

class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    code: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    created_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))
    created_by_user: Mapped[Optional[User]] = relationship("User", back_populates="created_subjects")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"),
                                                 onupdate=func.datetime("now"), nullable=False)

    lessons: Mapped[List["Lesson"]] = relationship("Lesson", back_populates="subject", cascade="all, delete-orphan", lazy="selectin")

class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    subject_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(LessonStatusEnum, default="draft", nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"),
                                                 onupdate=func.datetime("now"), nullable=False)

    subject: Mapped[Subject] = relationship("Subject", back_populates="lessons")
    blocks: Mapped[List["LessonBlock"]] = relationship("LessonBlock", back_populates="lesson",
                                                       cascade="all, delete-orphan", order_by="LessonBlock.position",
                                                       lazy="selectin")
    access_users: Mapped[List["LessonAccessUser"]] = relationship("LessonAccessUser", back_populates="lesson", cascade="all, delete-orphan")
    access_groups: Mapped[List["LessonAccessGroup"]] = relationship("LessonAccessGroup", back_populates="lesson", cascade="all, delete-orphan")
    views: Mapped[List["LessonView"]] = relationship("LessonView", back_populates="lesson")
    bookmarks: Mapped[List["Bookmark"]] = relationship("Bookmark", back_populates="lesson")

    __table_args__ = (Index("idx_lessons_subject", "subject_id"),)

class LessonBlock(Base):
    __tablename__ = "lesson_blocks"

    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(BlockTypeEnum, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    text_content: Mapped[Optional[str]] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    caption: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"),
                                                 onupdate=func.datetime("now"), nullable=False)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="blocks")

    __table_args__ = (
        UniqueConstraint("lesson_id", "position", name="uq_lesson_block_position"),
        Index("idx_lesson_blocks_lesson", "lesson_id", "position"),
    )

class LessonAccessUser(Base):
    __tablename__ = "lesson_access_users"
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="access_users")
    user: Mapped["User"] = relationship("User")
    __table_args__ = (Index("idx_access_users_user", "user_id"),)

class LessonAccessGroup(Base):
    __tablename__ = "lesson_access_groups"
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="access_groups")
    group: Mapped["Group"] = relationship("Group", back_populates="lesson_access")
    __table_args__ = (Index("idx_access_groups_group", "group_id"),)

class LessonView(Base):
    __tablename__ = "lesson_views"
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="views")
    user: Mapped["User"] = relationship("User")

class Bookmark(Base):
    __tablename__ = "bookmarks"
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="bookmarks")
    user: Mapped["User"] = relationship("User")

# Инициализация схемы
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
