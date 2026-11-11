"""
Conversation logging for Trivial.

Online:  set DATABASE_URL in your secrets to a Postgres connection string (e.g. from Neon).
Locally: with no DATABASE_URL, everything is saved to conversations.db in this folder.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text,
    create_engine, delete, func, insert, select, update,
)

LOCAL_DB_URL = f"sqlite:///{Path(__file__).parent / 'conversations.db'}"

metadata = MetaData()

conversations = Table(
    "conversations", metadata,
    Column("id", String(36), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("mode", String(20)),
    Column("subject", String(40)),
    Column("exam_board", String(40)),
    Column("model", String(60)),
    Column("is_admin", Boolean, default=False),   # your own test chats, so you can filter them out
)

messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("conversation_id", String(36), ForeignKey("conversations.id", ondelete="CASCADE"), index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("role", String(20), nullable=False),    # "user" or "assistant"
    Column("content", Text),
    Column("ran_code", Boolean, default=False),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("feedback", Integer),                   # 1 = thumbs up, 0 = thumbs down, empty = none
    Column("admin_note", Text),                    # your own notes on what to tweak
)


def normalise_url(url):
    """Use the psycopg (v3) driver for Postgres URLs such as the ones Neon gives you."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url=None):
    engine = create_engine(normalise_url(url or LOCAL_DB_URL), pool_pre_ping=True)
    metadata.create_all(engine)   # creates the tables the first time; harmless afterwards
    return engine


def now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def start_conversation(engine, mode, subject, exam_board, model, is_admin):
    conversation_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(insert(conversations).values(
            id=conversation_id, started_at=now(), mode=mode, subject=subject,
            exam_board=exam_board, model=model, is_admin=is_admin,
        ))
    return conversation_id


def add_message(engine, conversation_id, role, content, ran_code=False, input_tokens=None, output_tokens=None):
    with engine.begin() as conn:
        result = conn.execute(insert(messages).values(
            conversation_id=conversation_id, created_at=now(), role=role, content=content,
            ran_code=ran_code, input_tokens=input_tokens, output_tokens=output_tokens,
        ))
        return result.inserted_primary_key[0]


def set_feedback(engine, message_id, value):
    with engine.begin() as conn:
        conn.execute(update(messages).where(messages.c.id == message_id).values(feedback=value))


def set_note(engine, message_id, note):
    with engine.begin() as conn:
        conn.execute(update(messages).where(messages.c.id == message_id).values(admin_note=note or None))


def delete_conversation(engine, conversation_id):
    with engine.begin() as conn:
        conn.execute(delete(messages).where(messages.c.conversation_id == conversation_id))
        conn.execute(delete(conversations).where(conversations.c.id == conversation_id))


# ---------------------------------------------------------------------------
# Reading (for the review page)
# ---------------------------------------------------------------------------
def conversation_summary(engine):
    """One row per conversation with counts, feedback and a preview of the first question."""
    m = messages.c
    first = messages.alias("first_message")
    first_question = (
        select(first.c.content)
        .where(first.c.conversation_id == conversations.c.id, first.c.role == "user")
        .order_by(first.c.id).limit(1)
        .correlate(conversations)
        .scalar_subquery()
    )
    query = (
        select(
            conversations.c.id,
            conversations.c.started_at,
            conversations.c.mode,
            conversations.c.subject,
            conversations.c.exam_board,
            conversations.c.model,
            conversations.c.is_admin,
            func.count(m.id).filter(m.role == "user").label("questions"),
            func.count(m.id).filter(m.feedback == 1).label("thumbs_up"),
            func.count(m.id).filter(m.feedback == 0).label("thumbs_down"),
            func.count(m.id).filter(m.admin_note.is_not(None)).label("notes"),
            func.coalesce(func.sum(m.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(m.output_tokens), 0).label("output_tokens"),
            first_question.label("first_question"),
        )
        .select_from(conversations.outerjoin(messages, m.conversation_id == conversations.c.id))
        .group_by(conversations.c.id)
        .order_by(conversations.c.started_at.desc())
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def conversation_messages(engine, conversation_id):
    query = select(messages).where(messages.c.conversation_id == conversation_id).order_by(messages.c.id)
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


def search_conversation_ids(engine, text):
    """Conversations containing the given text in any message."""
    query = select(messages.c.conversation_id).where(messages.c.content.ilike(f"%{text}%")).distinct()
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(query)}


def export_all(engine):
    """Every message with its conversation details, for downloading as CSV."""
    query = (
        select(
            conversations.c.id.label("conversation_id"), conversations.c.started_at,
            conversations.c.mode, conversations.c.subject, conversations.c.exam_board,
            conversations.c.model, conversations.c.is_admin,
            messages.c.id.label("message_id"), messages.c.created_at, messages.c.role,
            messages.c.content, messages.c.ran_code, messages.c.feedback, messages.c.admin_note,
            messages.c.input_tokens, messages.c.output_tokens,
        )
        .select_from(conversations.join(messages, messages.c.conversation_id == conversations.c.id))
        .order_by(conversations.c.started_at, messages.c.id)
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn)
