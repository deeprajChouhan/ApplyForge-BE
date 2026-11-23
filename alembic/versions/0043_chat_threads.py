"""chat threads (Coach panel)

- application_chats becomes a thread table: the unique constraint on
  application_id is dropped (several threads per application), and it gains
  title, last_message_at, archived_at + index (user_id, application_id,
  last_message_at). application_id keeps a plain index for its FK.
- application_chat_messages gains user_id (FK users ON DELETE CASCADE,
  backfilled from the chat), status, citations_json, proposed_edit_json,
  context_json, applied_document_id (FK generated_documents ON DELETE SET
  NULL), tokens_in, tokens_out, model.

Existing rows become one thread per application with status 'complete'.
Consumer-only; no recruiter (rec_*) tables. No cross-product FKs.

The tables were created by metadata.create_all, so the unique constraint is
unnamed on SQLite and named after the column on MySQL: it is found by
reflection rather than by name.

Revision ID: 0043_chat_threads
Revises: 0042_application_notes
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0043_chat_threads"
down_revision = "0042_application_notes"
branch_labels = None
depends_on = None

CHATS = "application_chats"
MSGS = "application_chat_messages"
NC = {"uq": "uq_%(table_name)s_%(column_0_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s"}


def _insp():
    return sa.inspect(op.get_bind())


def _cols(table):
    return {c["name"] for c in _insp().get_columns(table)}


def _index_names(table):
    return {i["name"] for i in _insp().get_indexes(table)}


def _unique_on_application_id():
    insp = _insp()
    names = set()
    unnamed = False
    for u in insp.get_unique_constraints(CHATS):
        if u["column_names"] == ["application_id"]:
            if u.get("name"):
                names.add(u["name"])
            else:
                unnamed = True
    for i in insp.get_indexes(CHATS):
        if i.get("unique") and i["column_names"] == ["application_id"]:
            names.add(i["name"])
    return names, unnamed


def upgrade() -> None:
    insp = _insp()
    if not insp.has_table(CHATS) or not insp.has_table(MSGS):
        return  # fresh database: create_all builds the new shape from the models
    sqlite = op.get_bind().dialect.name == "sqlite"

    # ── threads ───────────────────────────────────────────────────────────
    if "ix_application_chats_application_id" not in _index_names(CHATS):
        op.create_index("ix_application_chats_application_id", CHATS, ["application_id"])
    names, unnamed = _unique_on_application_id()
    if sqlite and (names or unnamed):
        with op.batch_alter_table(CHATS, recreate="always", naming_convention=NC) as b:
            for n in names or {"uq_application_chats_application_id"}:
                b.drop_constraint(n, type_="unique")
    else:
        for n in names:
            op.drop_index(n, table_name=CHATS)

    chat_cols = _cols(CHATS)
    with op.batch_alter_table(CHATS) as b:
        if "title" not in chat_cols:
            b.add_column(sa.Column("title", sa.String(120), nullable=True))
        if "last_message_at" not in chat_cols:
            b.add_column(sa.Column("last_message_at", sa.DateTime(), nullable=True))
        if "archived_at" not in chat_cols:
            b.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
    if "ix_application_chats_user_app_last" not in _index_names(CHATS):
        op.create_index("ix_application_chats_user_app_last", CHATS, ["user_id", "application_id", "last_message_at"])

    # ── messages ──────────────────────────────────────────────────────────
    msg_cols = _cols(MSGS)
    new_user_id = "user_id" not in msg_cols
    with op.batch_alter_table(MSGS) as b:
        if new_user_id:
            b.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        if "status" not in msg_cols:
            b.add_column(sa.Column("status", sa.String(12), nullable=False, server_default="complete"))
        for name, typ in (("citations_json", sa.JSON()), ("proposed_edit_json", sa.JSON()), ("context_json", sa.JSON())):
            if name not in msg_cols:
                b.add_column(sa.Column(name, typ, nullable=True))
        if "applied_document_id" not in msg_cols:
            b.add_column(sa.Column("applied_document_id", sa.Integer(), nullable=True))
        for name in ("tokens_in", "tokens_out"):
            if name not in msg_cols:
                b.add_column(sa.Column(name, sa.Integer(), nullable=True))
        if "model" not in msg_cols:
            b.add_column(sa.Column("model", sa.String(60), nullable=True))

    if new_user_id:
        op.execute(
            f"UPDATE {MSGS} SET user_id = (SELECT c.user_id FROM {CHATS} c WHERE c.id = {MSGS}.chat_id)"
        )
        op.execute(f"DELETE FROM {MSGS} WHERE user_id IS NULL")  # orphans: no chat, no owner
        with op.batch_alter_table(MSGS, naming_convention=NC) as b:
            b.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
            b.create_foreign_key("fk_application_chat_messages_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE")
            b.create_foreign_key(
                "fk_application_chat_messages_applied_document_id",
                "generated_documents",
                ["applied_document_id"],
                ["id"],
                ondelete="SET NULL",
            )
        op.create_index("ix_application_chat_messages_user_id", MSGS, ["user_id"])

    op.execute(
        f"UPDATE {CHATS} SET last_message_at = "
        f"(SELECT MAX(m.created_at) FROM {MSGS} m WHERE m.chat_id = {CHATS}.id) WHERE last_message_at IS NULL"
    )


def downgrade() -> None:
    insp = _insp()
    if not insp.has_table(CHATS) or not insp.has_table(MSGS):
        return
    # Fold extra threads into each application's oldest one so the unique
    # constraint can come back. Messages are kept.
    op.execute(
        f"UPDATE {MSGS} SET chat_id = (SELECT MIN(c2.id) FROM {CHATS} c2 WHERE c2.application_id = "
        f"(SELECT c1.application_id FROM {CHATS} c1 WHERE c1.id = {MSGS}.chat_id))"
    )
    op.execute(f"DELETE FROM {CHATS} WHERE id NOT IN (SELECT MIN(id) FROM {CHATS} GROUP BY application_id)")

    msg_cols = _cols(MSGS)
    if "ix_application_chat_messages_user_id" in _index_names(MSGS):
        op.drop_index("ix_application_chat_messages_user_id", table_name=MSGS)
    with op.batch_alter_table(MSGS, recreate="always", naming_convention=NC) as b:
        for name in ("user_id", "status", "citations_json", "proposed_edit_json", "context_json",
                     "applied_document_id", "tokens_in", "tokens_out", "model"):
            if name in msg_cols:
                b.drop_column(name)

    if "ix_application_chats_user_app_last" in _index_names(CHATS):
        op.drop_index("ix_application_chats_user_app_last", table_name=CHATS)
    chat_cols = _cols(CHATS)
    with op.batch_alter_table(CHATS, recreate="always", naming_convention=NC) as b:
        for name in ("title", "last_message_at", "archived_at"):
            if name in chat_cols:
                b.drop_column(name)
        b.create_unique_constraint("uq_application_chats_application_id", ["application_id"])
    if "ix_application_chats_application_id" in _index_names(CHATS):
        op.drop_index("ix_application_chats_application_id", table_name=CHATS)
