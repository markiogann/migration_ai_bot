from datetime import datetime
from typing import Optional
import uuid
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Dialog(Base):
    __tablename__ = "dialogs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="dialog",
        cascade="all, delete-orphan",
    )
    __table_args__ = (
        Index("idx_dialogs_user_mode_active", "tg_user_id", "mode", "is_active"),
    )

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    home_country: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_country: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    migration_goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    budget: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    profession: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    boost_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dialog_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dialogs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dialog: Mapped["Dialog"] = relationship("Dialog", back_populates="messages")
    role: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("'chat'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        Index("idx_messages_user_id_id", "tg_user_id", "id"),
        Index("idx_messages_user_mode_role_created", "tg_user_id", "mode", "role", "created_at"),
        Index("idx_messages_dialog_id_id", "dialog_id", "id"),
    )

class CountryInfoCache(Base):
    __tablename__ = "country_info_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    country_query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        Index("idx_country_cache_key", "country_key"),
    )
