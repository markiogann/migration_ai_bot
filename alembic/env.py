import os
import asyncio
from logging.config import fileConfig
from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine
from dotenv import load_dotenv
from logic.models import Base

load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata

def _to_async_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("postgresql+asyncpg://"):
        return u
    if u.startswith("postgresql://"):
        return "postgresql+asyncpg://" + u[len("postgresql://") :]
    return u

def get_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return _to_async_url(url)

def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online() -> None:
    connectable = create_async_engine(get_url(), poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

def run_migrations() -> None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        asyncio.run(run_migrations_online())


run_migrations()
