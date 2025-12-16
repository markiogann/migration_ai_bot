"""add dialogs

Revision ID: 49a0f1dfb1fb
Revises: 0002_add_users_id_pk
Create Date: 2025-12-16 14:29:53.696637
"""
from typing import Sequence, Union
import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "49a0f1dfb1fb"
down_revision: Union[str, Sequence[str], None] = "0002_add_users_id_pk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def _has_table(bind, name: str) -> bool:
    return inspect(bind).has_table(name)

def _has_column(bind, table: str, column: str) -> bool:
    cols = [c["name"] for c in inspect(bind).get_columns(table)]
    return column in cols

def _constraint_exists(bind, name: str) -> bool:
    q = sa.text(
        "select 1 from pg_constraint where conname = :name limit 1"
    )
    return bind.execute(q, {"name": name}).scalar() is not None

def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "dialogs"):
        op.create_table(
            "dialogs",
            sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
            sa.Column("mode", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        bind.execute(sa.text("create index if not exists idx_dialogs_user_mode_active on dialogs (tg_user_id, mode, is_active)"))
        bind.execute(sa.text("create index if not exists ix_dialogs_tg_user_id on dialogs (tg_user_id)"))
        bind.execute(sa.text("create index if not exists ix_dialogs_mode on dialogs (mode)"))
    else:
        if not _has_column(bind, "dialogs", "is_active"):
            op.add_column("dialogs", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")))
        if not _has_column(bind, "dialogs", "created_at"):
            op.add_column("dialogs", sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")))
        if not _has_column(bind, "dialogs", "updated_at"):
            op.add_column("dialogs", sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")))
        bind.execute(sa.text("create index if not exists idx_dialogs_user_mode_active on dialogs (tg_user_id, mode, is_active)"))
    if not _has_column(bind, "messages", "dialog_id"):
        op.add_column("messages", sa.Column("dialog_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
        bind.execute(sa.text("create index if not exists idx_messages_dialog_id_id on messages (dialog_id, id)"))

        fk_name = "fk_messages_dialog_id_dialogs"
        if not _constraint_exists(bind, fk_name):
            bind.execute(sa.text(
                "alter table messages add constraint fk_messages_dialog_id_dialogs "
                "foreign key (dialog_id) references dialogs(id) on delete cascade"
            ))
        rows = bind.execute(sa.text("select distinct tg_user_id, mode from messages")).fetchall()
        for tg_user_id, mode in rows:
            did = uuid.uuid4()
            bind.execute(
                sa.text(
                    "insert into dialogs (id, tg_user_id, mode, is_active, created_at, updated_at) "
                    "values (:id, :tg_user_id, :mode, true, now(), now())"
                ),
                {"id": str(did), "tg_user_id": int(tg_user_id), "mode": str(mode)},
            )
            bind.execute(
                sa.text(
                    "update messages set dialog_id = :id where tg_user_id = :tg_user_id and mode = :mode and dialog_id is null"
                ),
                {"id": str(did), "tg_user_id": int(tg_user_id), "mode": str(mode)},
            )
        bind.execute(sa.text(
            "insert into dialogs (id, tg_user_id, mode, is_active, created_at, updated_at) "
            "select :id, tg_user_id, mode, true, now(), now() "
            "from (select tg_user_id, mode from messages where dialog_id is null limit 1) s"
        ), {"id": str(uuid.uuid4())})

        null_left = bind.execute(sa.text("select count(*) from messages where dialog_id is null")).scalar() or 0
        if int(null_left) == 0:
            op.alter_column("messages", "dialog_id", nullable=False)

def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "messages") and _has_column(bind, "messages", "dialog_id"):
        bind.execute(sa.text("drop index if exists idx_messages_dialog_id_id"))
        if _constraint_exists(bind, "fk_messages_dialog_id_dialogs"):
            bind.execute(sa.text("alter table messages drop constraint fk_messages_dialog_id_dialogs"))
        op.drop_column("messages", "dialog_id")
    if _has_table(bind, "dialogs"):
        bind.execute(sa.text("drop index if exists idx_dialogs_user_mode_active"))
        bind.execute(sa.text("drop index if exists ix_dialogs_tg_user_id"))
        bind.execute(sa.text("drop index if exists ix_dialogs_mode"))
        op.drop_table("dialogs")
