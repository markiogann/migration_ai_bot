import os
import asyncpg
from typing import Optional, Dict, List

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_pool: Optional[asyncpg.Pool] = None

# максимум сообщений на пользователя (user+assistant), старые будут удаляться
MAX_MESSAGES_PER_USER = 200


async def init_db():
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set in .env (DATABASE_URL=...)")

    _pool = await asyncpg.create_pool(DATABASE_URL)

    async with _pool.acquire() as conn:
        # пользователи
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                tg_user_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                home_country TEXT,
                target_country TEXT,
                migration_goal TEXT,
                budget TEXT,
                profession TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )

        # сообщения (добавили столбец mode)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                tg_user_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'chat',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )

        # кэш общей информации по странам
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS country_info_cache (
                id SERIAL PRIMARY KEY,
                country_key   TEXT UNIQUE NOT NULL,
                country_query TEXT NOT NULL,
                answer        TEXT NOT NULL,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def ensure_user(
    tg_user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    language_code: Optional[str],
):
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")

    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (
                tg_user_id, username, first_name, last_name, language_code
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (tg_user_id) DO UPDATE
            SET username     = EXCLUDED.username,
                first_name   = EXCLUDED.first_name,
                last_name    = EXCLUDED.last_name,
                language_code= EXCLUDED.language_code,
                updated_at   = NOW();
            """,
            tg_user_id,
            username,
            first_name,
            last_name,
            language_code,
        )


async def get_user_profile(tg_user_id: int) -> Optional[Dict]:
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE tg_user_id = $1;",
            tg_user_id,
        )
    if row is None:
        return None
    return dict(row)


async def update_user_profile(tg_user_id: int, **fields):
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")

    if not fields:
        return

    set_clauses = []
    values: List = []
    idx = 1

    for key, value in fields.items():
        set_clauses.append(f"{key} = ${idx}")
        values.append(value)
        idx += 1

    set_clauses.append("updated_at = NOW()")

    sql = f"UPDATE users SET {', '.join(set_clauses)} WHERE tg_user_id = ${idx}"
    values.append(tg_user_id)

    async with _pool.acquire() as conn:
        await conn.execute(sql, *values)


async def save_message(tg_user_id: int, role: str, text: str, mode: str = "chat"):
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")

    async with _pool.acquire() as conn:
        # сохраняем сообщение
        await conn.execute(
            """
            INSERT INTO messages (tg_user_id, role, text, mode)
            VALUES ($1, $2, $3, $4);
            """,
            tg_user_id,
            role,
            text,
            mode,
        )

        # обрезаем историю до MAX_MESSAGES_PER_USER
        await conn.execute(
            """
            DELETE FROM messages
            WHERE id IN (
                SELECT id
                FROM messages
                WHERE tg_user_id = $1
                ORDER BY created_at ASC
                OFFSET $2
            );
            """,
            tg_user_id,
            MAX_MESSAGES_PER_USER,
        )


async def get_recent_messages(tg_user_id: int, limit: int = 6) -> List[Dict]:
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, text, created_at
            FROM messages
            WHERE tg_user_id = $1
            ORDER BY id DESC
            LIMIT $2;
            """,
            tg_user_id,
            limit,
        )

    return [dict(r) for r in reversed(rows)]


async def get_daily_user_message_count(tg_user_id: int, mode: str) -> int:
    """
    Считаем, сколько сообщений пользователь отправил СЕГОДНЯ в указанном режиме.
    """
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with _pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE tg_user_id = $1
              AND role = 'user'
              AND mode = $2
              AND created_at::date = CURRENT_DATE;
            """,
            tg_user_id,
            mode,
        )
    return int(value or 0)


def _normalize_country_key(raw: str) -> str:
    return (raw or "").strip().lower()


async def get_cached_country_info(country_key: str) -> Optional[str]:
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")

    key = _normalize_country_key(country_key)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT answer FROM country_info_cache WHERE country_key = $1;",
            key,
        )

    if row is None:
        return None
    return row["answer"]


async def save_cached_country_info(country_key: str, country_query: str, answer: str):
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")

    key = _normalize_country_key(country_key)

    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO country_info_cache (country_key, country_query, answer)
            VALUES ($1, $2, $3)
            ON CONFLICT (country_key) DO UPDATE
            SET country_query = EXCLUDED.country_query,
                answer        = EXCLUDED.answer,
                created_at    = NOW();
            """,
            key,
            country_query,
            answer,
        )
