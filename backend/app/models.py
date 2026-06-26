"""SQLAlchemy ORM models — the normalized, platform-neutral store.

These mirror the authoritative DDL in CLAUDE.md ("Concrete schema"). The hand-
written first migration creates the Postgres enum types, the `vector`/`pgcrypto`
extensions, and these tables; the models here are the in-app view of that schema.

Design rules carried from CLAUDE.md:
  - Surrogate UUID primary keys (`gen_random_uuid()`), never platform-native ids.
  - Every table has `created_at` / `updated_at` (timestamptz, default now()).
  - Platform-native ids are their own columns.
  - Every row traces back to a `creator` so ownership is a clean join.
  - Encrypted secrets are `bytea` (Fernet ciphertext); the DB never decrypts.
"""

from __future__ import annotations

import datetime
import enum
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text  # aliased: a `text` column would shadow it
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


# --- Shared category vocabulary -------------------------------------------------
# One constrained enum used identically by the classifier, the policy table, and
# the voice tags. Keep these names in lockstep with the Postgres `comment_category`
# type created in the migration.
class CommentCategory(str, enum.Enum):
    spam = "spam"
    simple_positive = "simple_positive"
    question = "question"
    brand_inquiry = "brand_inquiry"
    criticism = "criticism"
    sensitive = "sensitive"
    other = "other"


class PolicyAction(str, enum.Enum):
    reply = "reply"
    hide = "hide"
    like = "like"
    ignore = "ignore"
    flag = "flag"


class AutonomyLevel(str, enum.Enum):
    auto_send = "auto_send"
    draft = "draft"
    notify_only = "notify_only"


class ActionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    sent = "sent"
    rejected = "rejected"
    auto_sent = "auto_sent"
    uncertain = "uncertain"
    failed = "failed"


class PlatformKind(str, enum.Enum):
    youtube = "youtube"


# Reusable PG enum type bindings. create_type=False: the hand-written migration
# owns creation of these types, so SQLAlchemy must not try to CREATE TYPE again
# (which would error on a second referencing table).
comment_category_enum = PgEnum(
    CommentCategory, name="comment_category", create_type=False
)
policy_action_enum = PgEnum(PolicyAction, name="policy_action", create_type=False)
autonomy_level_enum = PgEnum(AutonomyLevel, name="autonomy_level", create_type=False)
action_status_enum = PgEnum(ActionStatus, name="action_status", create_type=False)
platform_kind_enum = PgEnum(PlatformKind, name="platform_kind", create_type=False)


# --- Common column helpers ------------------------------------------------------
def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )


class TimestampMixin:
    """created_at / updated_at, both timestamptz, defaulted in the DB."""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# --- Tables ---------------------------------------------------------------------
class Creator(TimestampMixin, Base):
    """App users. The root of every ownership chain."""

    __tablename__ = "creators"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    # bcrypt/argon2 hash only — plaintext is never stored or logged (Phase 2).
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    platform_accounts: Mapped[list[PlatformAccount]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    llm_credentials: Mapped[list[LlmCredential]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    policies: Mapped[list[CategoryPolicy]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    actions: Mapped[list[Action]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )


class PlatformAccount(TimestampMixin, Base):
    """One connected social account. Many-per-creator, each tagged by platform —
    this is what makes the store structurally multi-platform."""

    __tablename__ = "platform_accounts"
    __table_args__ = (
        UniqueConstraint(
            "platform", "platform_account_id", name="uq_platform_account_native"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[PlatformKind] = mapped_column(platform_kind_enum, nullable=False)
    platform_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Encrypted OAuth tokens (Fernet ciphertext). The DB never decrypts.
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'connected'")
    )  # connected | needs_reauth

    creator: Mapped[Creator] = relationship(back_populates="platform_accounts")
    posts: Mapped[list[Post]] = relationship(
        back_populates="platform_account", cascade="all, delete-orphan"
    )
    voice_examples: Mapped[list[VoiceExample]] = relationship(
        back_populates="platform_account", cascade="all, delete-orphan"
    )


class LlmCredential(TimestampMixin, Base):
    """A creator's bring-your-own LLM provider/model + encrypted API key."""

    __tablename__ = "llm_credentials"

    id: Mapped[uuid.UUID] = _uuid_pk()
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)  # 'anthropic' etc.
    model: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Masked display form only, e.g. 'sk-...4f2a'. Never the full key.
    key_hint: Mapped[str] = mapped_column(Text, nullable=False)

    creator: Mapped[Creator] = relationship(back_populates="llm_credentials")


class Post(TimestampMixin, Base):
    """A platform-neutral post (a YouTube video in v1)."""

    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint(
            "platform_account_id", "platform_post_id", name="uq_post_native"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform_post_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    platform_account: Mapped[PlatformAccount] = relationship(back_populates="posts")
    comments: Mapped[list[Comment]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )


class Comment(TimestampMixin, Base):
    """A normalized comment. `category` + `confidence` MUST travel into the
    autonomy gate, so they live on the comment row itself."""

    __tablename__ = "comments"
    __table_args__ = (
        UniqueConstraint(
            "post_id", "platform_comment_id", name="uq_comment_native"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform_comment_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Self-FK: null = top-level comment, otherwise the parent comment row.
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comments.id", ondelete="CASCADE")
    )
    author_name: Mapped[str | None] = mapped_column(Text)
    author_channel_id: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_reply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    # The creator's own replies are voice data.
    authored_by_creator: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    published_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    category: Mapped[CommentCategory | None] = mapped_column(
        comment_category_enum
    )  # null until classified
    confidence: Mapped[float | None] = mapped_column(Float)  # 0..1
    classified_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    post: Mapped[Post] = relationship(back_populates="comments")
    parent: Mapped[Comment | None] = relationship(
        remote_side="Comment.id", back_populates="replies"
    )
    replies: Mapped[list[Comment]] = relationship(back_populates="parent")
    action: Mapped[Action | None] = relationship(
        back_populates="comment", cascade="all, delete-orphan", uselist=False
    )


class VoiceExample(TimestampMixin, Base):
    """A (parent comment -> creator reply) pair with an embedding for semantic
    retrieval. Its OWN denormalized row (not an FK into comments): the voice store
    is rebuilt on refresh and has a different lifecycle than the live feed."""

    __tablename__ = "voice_examples"
    __table_args__ = (
        # Idempotent refresh: re-running the job never duplicates an example.
        UniqueConstraint(
            "platform_account_id",
            "category",
            "parent_comment_text",
            "reply_text",
            name="uq_voice_example_dedup",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[CommentCategory] = mapped_column(
        comment_category_enum, nullable=False
    )
    parent_comment_text: Mapped[str] = mapped_column(Text, nullable=False)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Dimension must match the embedding model used by the voice pipeline (Phase 6).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    source_published_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # for recency-weighted retrieval

    platform_account: Mapped[PlatformAccount] = relationship(
        back_populates="voice_examples"
    )


class CategoryPolicy(TimestampMixin, Base):
    """Per-category routing policy the creator controls. The autonomy gate reads
    this on every write decision (Phase 8)."""

    __tablename__ = "category_policies"
    __table_args__ = (
        UniqueConstraint(
            "creator_id", "platform", "category", name="uq_policy_per_category"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[PlatformKind] = mapped_column(platform_kind_enum, nullable=False)
    category: Mapped[CommentCategory] = mapped_column(
        comment_category_enum, nullable=False
    )
    action: Mapped[PolicyAction] = mapped_column(policy_action_enum, nullable=False)
    autonomy: Mapped[AutonomyLevel] = mapped_column(
        autonomy_level_enum, nullable=False
    )
    tone_constraint: Mapped[str | None] = mapped_column(Text)

    creator: Mapped[Creator] = relationship(back_populates="policies")


class Action(TimestampMixin, Base):
    """The approval queue AND the audit log AND the idempotency guard, in one
    table. UNIQUE(comment_id) is the idempotency backstop — one action per
    comment, so a write can never be executed twice."""

    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("comment_id", name="uq_action_per_comment"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=False,
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[CommentCategory] = mapped_column(
        comment_category_enum, nullable=False
    )  # routing category at decision time
    autonomy_decision: Mapped[AutonomyLevel] = mapped_column(
        autonomy_level_enum, nullable=False
    )
    # Original LLM draft is preserved; final_text holds the creator's edit. The
    # draft->final delta is the highest-quality voice-correction signal.
    draft_text: Mapped[str | None] = mapped_column(Text)
    final_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ActionStatus] = mapped_column(
        action_status_enum, nullable=False, server_default=sql_text("'pending'")
    )
    platform_reply_id: Mapped[str | None] = mapped_column(Text)
    approver: Mapped[str | None] = mapped_column(Text)  # creator id / 'auto'
    # Scrubbed before write — never contains a secret.
    error_detail: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    comment: Mapped[Comment] = relationship(back_populates="action")
    creator: Mapped[Creator] = relationship(back_populates="actions")
