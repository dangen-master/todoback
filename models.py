from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Integer, SmallInteger, String, Text, Boolean, DateTime, Enum,
    ForeignKey, UniqueConstraint, Index, CheckConstraint, func, text, event
)
from sqlalchemy import BigInteger as SA_BigInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ---------- Async engine (SQLite dev) ----------
engine = create_async_engine("sqlite+aiosqlite:///db.sqlite3", echo=True)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False)

# Enable foreign keys in SQLite
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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

# ---------- Common column helpers ----------
PK_INT = Integer
FK_INT = Integer


# ---------- Mixins ----------
class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.datetime("now"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.datetime("now"),
        onupdate=func.datetime("now"), nullable=False
    )


# ---------- Models ----------
class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(SA_BigInteger, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    avatar_url: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))

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


class Group(TimestampMixin, Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    members: Mapped[List[User]] = relationship("User", secondary="group_members", back_populates="groups", lazy="selectin")
    lesson_access: Mapped[List["LessonAccessGroup"]] = relationship("LessonAccessGroup", back_populates="group")


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_in_group: Mapped[Optional[str]] = mapped_column(String)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.datetime("now"), nullable=False)

    __table_args__ = (Index("idx_group_members_user", "user_id"),)


class Subject(TimestampMixin, Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    code: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    created_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))
    created_by_user: Mapped[Optional[User]] = relationship("User", back_populates="created_subjects")

    lessons: Mapped[List["Lesson"]] = relationship(
        "Lesson", back_populates="subject", cascade="all, delete-orphan", lazy="selectin"
    )


class Lesson(TimestampMixin, Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(PK_INT, primary_key=True)
    subject_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(LessonStatusEnum, nullable=False, server_default=text("'draft'"))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))

    created_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))

    subject: Mapped[Subject] = relationship("Subject", back_populates="lessons")
    blocks: Mapped[List["LessonBlock"]] = relationship(
        "LessonBlock", back_populates="lesson",
        cascade="all, delete-orphan",
        order_by="LessonBlock.position",
        lazy="selectin"
    )
    access_users: Mapped[List["LessonAccessUser"]] = relationship(
        "LessonAccessUser", back_populates="lesson", cascade="all, delete-orphan"
    )
    access_groups: Mapped[List["LessonAccessGroup"]] = relationship(
        "LessonAccessGroup", back_populates="lesson", cascade="all, delete-orphan"
    )
    views: Mapped[List["LessonView"]] = relationship("LessonView", back_populates="lesson")
    bookmarks: Mapped[List["Bookmark"]] = relationship("Bookmark", back_populates="lesson")

    __table_args__ = (Index("idx_lessons_subject", "subject_id"),)


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
    granted_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.datetime("now"), nullable=False)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="access_users")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])                       # ✅ фикс
    granted_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[granted_by])  # ✅ фикс

    __table_args__ = (
        Index("idx_access_users_user", "user_id"),
        Index("idx_access_users_lesson", "lesson_id"),
    )


class LessonAccessGroup(Base):
    __tablename__ = "lesson_access_groups"

    lesson_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[int] = mapped_column(FK_INT, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[Optional[int]] = mapped_column(FK_INT, ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.datetime("now"), nullable=False)

    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="access_groups")
    group: Mapped["Group"] = relationship("Group", back_populates="lesson_access")
    granted_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[granted_by])  # ✅ фикс

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


# ---------- Schema init ----------
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
