"""fix dialogs schema
Revision ID: 18cfa623f598
Revises: 49a0f1dfb1fb
Create Date: 2025-12-16 15:06:52.085981
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "18cfa623f598"
down_revision: Union[str, Sequence[str], None] = "49a0f1dfb1fb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return inspect(bind).has_table(name)


def _has_column(bind, table: str, column: str) -> bool:
    cols = [c["name"] for c in inspect(bind).get_columns(table)]
    return column in cols


def _constraint_exists(bind, name: str) -> bool:
    q = sa.text("select 1 from pg_constraint where conname = :name limit 1")
    return bind.execute(q, {"name": name}).scalar() is not None


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "dialogs"):
        op.execute(sa.text("""
            create table dialogs (
                id uuid primary key,
                tg_user_id bigint not null,
                mode text not null,
                is_active boolean not null default true,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
        """))
        op.execute(sa.text("create index if not exists idx_dialogs_user_mode_active on dialogs (tg_user_id, mode, is_active)"))
        op.execute(sa.text("create index if not exists ix_dialogs_tg_user_id on dialogs (tg_user_id)"))
        op.execute(sa.text("create index if not exists ix_dialogs_mode on dialogs (mode)"))
    else:
        if not _has_column(bind, "dialogs", "is_active"):
            op.execute(sa.text("alter table dialogs add column is_active boolean not null default true"))
        if not _has_column(bind, "dialogs", "created_at"):
            op.execute(sa.text("alter table dialogs add column created_at timestamptz not null default now()"))
        if not _has_column(bind, "dialogs", "updated_at"):
            op.execute(sa.text("alter table dialogs add column updated_at timestamptz not null default now()"))

        op.execute(sa.text("create index if not exists idx_dialogs_user_mode_active on dialogs (tg_user_id, mode, is_active)"))

    if _has_table(bind, "messages") and not _has_column(bind, "messages", "dialog_id"):
        op.execute(sa.text("alter table messages add column dialog_id uuid"))
        op.execute(sa.text("create index if not exists idx_messages_dialog_id_id on messages (dialog_id, id)"))

        fk_name = "fk_messages_dialog_id_dialogs"
        if not _constraint_exists(bind, fk_name):
            op.execute(sa.text(
                "alter table messages add constraint fk_messages_dialog_id_dialogs "
                "foreign key (dialog_id) references dialogs(id) on delete cascade"
            ))

        rows = bind.execute(sa.text("select distinct tg_user_id, mode from messages")).fetchall()
        for tg_user_id, mode in rows:
            existing = bind.execute(
                sa.text("select id from dialogs where tg_user_id = :u and mode = :m order by created_at desc limit 1"),
                {"u": int(tg_user_id), "m": str(mode)},
            ).scalar()

            did = existing or uuid.uuid4()
            if not existing:
                bind.execute(
                    sa.text(
                        "insert into dialogs (id, tg_user_id, mode, is_active, created_at, updated_at) "
                        "values (:id, :u, :m, true, now(), now())"
                    ),
                    {"id": str(did), "u": int(tg_user_id), "m": str(mode)},
                )

            bind.execute(
                sa.text(
                    "update messages set dialog_id = :id "
                    "where tg_user_id = :u and mode = :m and dialog_id is null"
                ),
                {"id": str(did), "u": int(tg_user_id), "m": str(mode)},
            )

        null_left = bind.execute(sa.text("select count(*) from messages where dialog_id is null")).scalar() or 0
        if int(null_left) == 0:
            op.execute(sa.text("alter table messages alter column dialog_id set not null"))


def downgrade() -> None:
    pass
