import os
from typing import Optional
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None

def _to_async_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("postgresql+asyncpg://"):
        return u
    if u.startswith("postgresql://"):
        return "postgresql+asyncpg://" + u[len("postgresql://") :]
    return u

def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set in .env (DATABASE_URL=...)")
        _engine = create_async_engine(_to_async_url(DATABASE_URL), future=True, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine

def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker

async def dispose_engine():
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
