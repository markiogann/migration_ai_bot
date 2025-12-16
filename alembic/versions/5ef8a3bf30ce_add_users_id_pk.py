from alembic import op
import sqlalchemy as sa

revision = "0002_add_users_id_pk"
down_revision = "0001_init_schema"
branch_labels = None
depends_on = None

def upgrade():
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='users' AND column_name='id'
            ) THEN
                ALTER TABLE users ADD COLUMN id SERIAL;
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_name='users' AND constraint_type='PRIMARY KEY'
            ) THEN
                ALTER TABLE users ADD CONSTRAINT users_pkey PRIMARY KEY (id);
            END IF;
        END$$;
        """
    )

def downgrade():
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_name='users' AND constraint_name='users_pkey'
            ) THEN
                ALTER TABLE users DROP CONSTRAINT users_pkey;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='users' AND column_name='id'
            ) THEN
                ALTER TABLE users DROP COLUMN id;
            END IF;
        END$$;
        """
    )
