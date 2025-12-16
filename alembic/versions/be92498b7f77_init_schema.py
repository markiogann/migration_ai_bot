from alembic import op
import sqlalchemy as sa

revision = "0001_init_schema"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("language_code", sa.Text(), nullable=True),
        sa.Column("home_country", sa.Text(), nullable=True),
        sa.Column("target_country", sa.Text(), nullable=True),
        sa.Column("migration_goal", sa.Text(), nullable=True),
        sa.Column("budget", sa.Text(), nullable=True),
        sa.Column("profession", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("boost_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), server_default="chat", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_table(
        "country_info_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_key", sa.Text(), nullable=False, unique=True),
        sa.Column("country_query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_messages_user_id_id", "messages", ["tg_user_id", "id"])
    op.create_index("idx_messages_user_mode_role_created", "messages", ["tg_user_id", "mode", "role", "created_at"])
    op.create_index("idx_country_cache_key", "country_info_cache", ["country_key"])

def downgrade():
    op.drop_index("idx_country_cache_key", table_name="country_info_cache")
    op.drop_index("idx_messages_user_mode_role_created", table_name="messages")
    op.drop_index("idx_messages_user_id_id", table_name="messages")
    op.drop_table("country_info_cache")
    op.drop_table("messages")
    op.drop_table("users")
