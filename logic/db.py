import os
import asyncpg
from typing import Optional, Dict, List
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_pool: Optional[asyncpg.Pool] = None

MAX_MESSAGES_PER_USER = 200

COUNTRY_CACHE_TTL_DAYS = int(os.getenv("COUNTRY_CACHE_TTL_DAYS", "45"))

ALLOWED_PROFILE_FIELDS = {
    "username",
    "first_name",
    "last_name",
    "language_code",
    "home_country",
    "target_country",
    "migration_goal",
    "budget",
    "profession",
    "notes",
    "boost_until",
}


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")
    return _pool


async def init_db():
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set in .env (DATABASE_URL=...)")

    _pool = await asyncpg.create_pool(DATABASE_URL)

    pool = _require_pool()
    async with pool.acquire() as conn:
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

        await conn.execute(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS boost_until TIMESTAMPTZ;
            """
        )

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

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_user_id_id ON messages (tg_user_id, id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_user_mode_role_created ON messages (tg_user_id, mode, role, created_at);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_country_cache_key ON country_info_cache (country_key);"
        )
        await conn.execute(
            """
            DELETE FROM country_info_cache
            WHERE created_at < NOW() - ($1 * INTERVAL '1 day');
            """,
            COUNTRY_CACHE_TTL_DAYS,
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
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (
                tg_user_id, username, first_name, last_name, language_code
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (tg_user_id) DO UPDATE
            SET username      = EXCLUDED.username,
                first_name    = EXCLUDED.first_name,
                last_name     = EXCLUDED.last_name,
                language_code = EXCLUDED.language_code,
                updated_at    = NOW();
            """,
            tg_user_id,
            username,
            first_name,
            last_name,
            language_code,
        )


async def get_user_profile(tg_user_id: int) -> Optional[Dict]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE tg_user_id = $1;",
            tg_user_id,
        )
    return dict(row) if row else None


async def update_user_profile(tg_user_id: int, **fields):
    pool = _require_pool()
    if not fields:
        return

    for key in list(fields.keys()):
        if key not in ALLOWED_PROFILE_FIELDS:
            raise ValueError(f"Invalid profile field: {key}")

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

    async with pool.acquire() as conn:
        await conn.execute(sql, *values)


async def save_message(tg_user_id: int, role: str, text: str, mode: str = "chat"):
    pool = _require_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
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

            await conn.execute(
                """
                DELETE FROM messages
                WHERE id IN (
                    SELECT id
                    FROM messages
                    WHERE tg_user_id = $1
                    ORDER BY id DESC
                    OFFSET $2
                );
                """,
                tg_user_id,
                MAX_MESSAGES_PER_USER,
            )

async def get_recent_messages(tg_user_id: int, limit: int = 6, mode: Optional[str] = None) -> List[Dict]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        if mode:
            rows = await conn.fetch(
                """
                SELECT role, text, created_at
                FROM messages
                WHERE tg_user_id = $1 AND mode = $2
                ORDER BY id DESC
                LIMIT $3;
                """,
                tg_user_id,
                mode,
                limit,
            )
        else:
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
    pool = _require_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE tg_user_id = $1
              AND role = 'user'
              AND mode = $2
              AND (created_at AT TIME ZONE 'UTC')::date = (NOW() AT TIME ZONE 'UTC')::date;
            """,
            tg_user_id,
            mode,
        )
    return int(value or 0)


def _normalize_country_key(raw: str) -> str:
    return (raw or "").strip().lower()


async def get_cached_country_info(country_key: str) -> Optional[str]:
    pool = _require_pool()
    key = _normalize_country_key(country_key)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT answer
            FROM country_info_cache
            WHERE country_key = $1
              AND created_at >= NOW() - ($2 * INTERVAL '1 day');
            """,
            key,
            COUNTRY_CACHE_TTL_DAYS,
        )
    return row["answer"] if row else None


async def save_cached_country_info(country_key: str, country_query: str, answer: str):
    pool = _require_pool()
    key = _normalize_country_key(country_key)

    async with pool.acquire() as conn:
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
        await conn.execute(
            """
            DELETE FROM country_info_cache
            WHERE created_at < NOW() - ($1 * INTERVAL '1 day');
            """,
            COUNTRY_CACHE_TTL_DAYS,
        )

async def delete_cached_country_info(country_key: str) -> None:
    if _pool is None:
        raise RuntimeError("DB pool is not initialized")

    key = _normalize_country_key(country_key)

    async with _pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM country_info_cache WHERE country_key = $1;",
            key,
        )


async def get_user_boost_until(tg_user_id: int) -> Optional[datetime]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT boost_until FROM users WHERE tg_user_id = $1;",
            tg_user_id,
        )
    return value


async def add_boost_days(tg_user_id: int, days: int = 7):
    pool = _require_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET boost_until = GREATEST(COALESCE(boost_until, NOW()), NOW()) + ($2 * INTERVAL '1 day'),
                updated_at  = NOW()
            WHERE tg_user_id = $1;
            """,
            tg_user_id,
            int(days),
        )
from datetime import timedelta

async def admin_get_stats() -> Dict[str, int]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users;")
        new_today = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            WHERE (created_at AT TIME ZONE 'UTC')::date = (NOW() AT TIME ZONE 'UTC')::date;
            """
        )
        chat_today = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE role = 'user'
              AND mode = 'chat'
              AND (created_at AT TIME ZONE 'UTC')::date = (NOW() AT TIME ZONE 'UTC')::date;
            """
        )
        country_today = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE role = 'user'
              AND mode = 'country'
              AND (created_at AT TIME ZONE 'UTC')::date = (NOW() AT TIME ZONE 'UTC')::date;
            """
        )
        boosts_active = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE boost_until IS NOT NULL AND boost_until > NOW();"
        )
        cache_size = await conn.fetchval("SELECT COUNT(*) FROM country_info_cache;")

    return {
        "total_users": int(total_users or 0),
        "new_today": int(new_today or 0),
        "chat_today": int(chat_today or 0),
        "country_today": int(country_today or 0),
        "boosts_active": int(boosts_active or 0),
        "cache_size": int(cache_size or 0),
    }


async def admin_get_user(tg_user_id: int) -> Optional[Dict]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE tg_user_id = $1;", tg_user_id)
    return dict(row) if row else None


async def admin_find_users_by_username(query: str, limit: int = 10) -> List[Dict]:
    pool = _require_pool()
    q = (query or "").strip().lstrip("@")
    if not q:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tg_user_id, username, first_name, last_name, created_at
            FROM users
            WHERE username ILIKE $1
            ORDER BY created_at DESC
            LIMIT $2;
            """,
            f"%{q}%",
            limit,
        )
    return [dict(r) for r in rows]


async def admin_get_user_today_counts(tg_user_id: int) -> Dict[str, int]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        chat_used = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE tg_user_id = $1
              AND role = 'user'
              AND mode = 'chat'
              AND (created_at AT TIME ZONE 'UTC')::date = (NOW() AT TIME ZONE 'UTC')::date;
            """,
            tg_user_id,
        )
        country_used = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE tg_user_id = $1
              AND role = 'user'
              AND mode = 'country'
              AND (created_at AT TIME ZONE 'UTC')::date = (NOW() AT TIME ZONE 'UTC')::date;
            """,
            tg_user_id,
        )
    return {"chat": int(chat_used or 0), "country": int(country_used or 0)}


async def admin_clear_boost(tg_user_id: int):
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET boost_until = NULL,
                updated_at = NOW()
            WHERE tg_user_id = $1;
            """,
            tg_user_id,
        )


async def admin_list_cache(query: str, limit: int = 10) -> List[Dict]:
    pool = _require_pool()
    q = (query or "").strip()
    async with pool.acquire() as conn:
        if q:
            rows = await conn.fetch(
                """
                SELECT country_key, country_query, created_at
                FROM country_info_cache
                WHERE country_key ILIKE $1 OR country_query ILIKE $1
                ORDER BY created_at DESC
                LIMIT $2;
                """,
                f"%{q}%",
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT country_key, country_query, created_at
                FROM country_info_cache
                ORDER BY created_at DESC
                LIMIT $1;
                """,
                limit,
            )
    return [dict(r) for r in rows]


async def admin_delete_cache(country_key: str):
    pool = _require_pool()
    key = _normalize_country_key(country_key)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM country_info_cache WHERE country_key = $1;", key)


async def admin_get_all_user_ids() -> List[int]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tg_user_id FROM users;")
    return [int(r["tg_user_id"]) for r in rows]
