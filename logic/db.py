import os
import uuid
from typing import Optional, Dict, List
from datetime import datetime
from dotenv import load_dotenv

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from logic.database import get_sessionmaker, dispose_engine
from logic.models import User, Message, CountryInfoCache, Dialog

load_dotenv()

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

def _normalize_country_key(raw: str) -> str:
    return (raw or "").strip().lower()

async def init_db():
    Session = get_sessionmaker()
    async with Session() as session:
        await session.execute(
            delete(CountryInfoCache).where(
                CountryInfoCache.created_at < (func.now() - text(f"INTERVAL '{COUNTRY_CACHE_TTL_DAYS} day'"))
            )
        )
        await session.commit()

async def close_db():
    await dispose_engine()

async def ensure_user(
    tg_user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    language_code: Optional[str],
):
    Session = get_sessionmaker()
    async with Session() as session:
        stmt = (
            insert(User)
            .values(
                tg_user_id=tg_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
                updated_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=[User.tg_user_id],
                set_={
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "language_code": language_code,
                    "updated_at": func.now(),
                },
            )
        )
        await session.execute(stmt)
        await session.commit()

async def start_new_dialog(tg_user_id: int, mode: str = "chat") -> str:
    dialog_id = uuid.uuid4()
    Session = get_sessionmaker()
    async with Session() as session:
        await session.execute(
            update(Dialog)
            .where(Dialog.tg_user_id == tg_user_id, Dialog.mode == mode, Dialog.is_active.is_(True))
            .values(is_active=False, updated_at=func.now())
        )
        await session.execute(
            insert(Dialog).values(
                id=dialog_id,
                tg_user_id=tg_user_id,
                mode=mode,
                is_active=True,
                created_at=func.now(),
                updated_at=func.now(),
            )
        )
        await session.commit()
    return str(dialog_id)

async def get_active_dialog_id(tg_user_id: int, mode: str = "chat") -> str:
    Session = get_sessionmaker()
    async with Session() as session:
        stmt = (
            select(Dialog.id)
            .where(Dialog.tg_user_id == tg_user_id, Dialog.mode == mode, Dialog.is_active.is_(True))
            .order_by(Dialog.updated_at.desc())
            .limit(1)
        )
        value = (await session.execute(stmt)).scalar_one_or_none()
        if value:
            return str(value)
    return await start_new_dialog(tg_user_id, mode)

async def get_user_profile(tg_user_id: int) -> Optional[Dict]:
    Session = get_sessionmaker()
    async with Session() as session:
        row = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
        u = row.scalar_one_or_none()
        if not u:
            return None
        return {
            "id": u.id,
            "tg_user_id": u.tg_user_id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "language_code": u.language_code,
            "home_country": u.home_country,
            "target_country": u.target_country,
            "migration_goal": u.migration_goal,
            "budget": u.budget,
            "profession": u.profession,
            "notes": u.notes,
            "boost_until": u.boost_until,
            "created_at": u.created_at,
            "updated_at": u.updated_at,
        }

async def update_user_profile(tg_user_id: int, **fields):
    if not fields:
        return
    for key in list(fields.keys()):
        if key not in ALLOWED_PROFILE_FIELDS:
            raise ValueError(f"Invalid profile field: {key}")
    fields["updated_at"] = func.now()
    Session = get_sessionmaker()
    async with Session() as session:
        await session.execute(update(User).where(User.tg_user_id == tg_user_id).values(**fields))
        await session.commit()

async def save_message(
    tg_user_id: int,
    role: str,
    text_value: str,
    mode: str = "chat",
    dialog_id: Optional[str] = None,
):
    if not dialog_id:
        dialog_id = await get_active_dialog_id(tg_user_id, mode)
    dialog_uuid = uuid.UUID(str(dialog_id))
    Session = get_sessionmaker()
    async with Session() as session:
        await session.execute(
            insert(Message).values(
                tg_user_id=tg_user_id,
                dialog_id=dialog_uuid,
                role=role,
                text=text_value,
                mode=mode,
            )
        )
        await session.execute(
            update(Dialog)
            .where(Dialog.id == dialog_uuid)
            .values(updated_at=func.now())
        )
        subq = (
            select(Message.id)
            .where(Message.dialog_id == dialog_uuid)
            .order_by(Message.id.desc())
            .offset(MAX_MESSAGES_PER_USER)
            .subquery()
        )
        await session.execute(delete(Message).where(Message.id.in_(select(subq.c.id))))
        await session.commit()

async def get_recent_messages(
    tg_user_id: int,
    limit: int = 6,
    mode: Optional[str] = None,
    dialog_id: Optional[str] = None,
) -> List[Dict]:
    use_mode = mode or "chat"
    if not dialog_id:
        dialog_id = await get_active_dialog_id(tg_user_id, use_mode)
    dialog_uuid = uuid.UUID(str(dialog_id))
    Session = get_sessionmaker()
    async with Session() as session:
        stmt = (
            select(Message.role, Message.text, Message.created_at)
            .where(Message.dialog_id == dialog_uuid)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
    out = [{"role": r[0], "text": r[1], "created_at": r[2]} for r in rows]
    out.reverse()
    return out

async def get_daily_user_message_count(tg_user_id: int, mode: str) -> int:
    Session = get_sessionmaker()
    async with Session() as session:
        stmt = (
            select(func.count())
            .select_from(Message)
            .where(
                Message.tg_user_id == tg_user_id,
                Message.role == "user",
                Message.mode == mode,
                func.date(func.timezone("UTC", Message.created_at)) == func.date(func.timezone("UTC", func.now())),
            )
        )
        value = (await session.execute(stmt)).scalar_one()
    return int(value or 0)

async def get_cached_country_info(country_key: str) -> Optional[str]:
    key = _normalize_country_key(country_key)
    Session = get_sessionmaker()
    async with Session() as session:
        stmt = (
            select(CountryInfoCache.answer)
            .where(
                CountryInfoCache.country_key == key,
                CountryInfoCache.created_at >= (func.now() - text(f"INTERVAL '{COUNTRY_CACHE_TTL_DAYS} day'")),
            )
            .limit(1)
        )
        value = (await session.execute(stmt)).scalar_one_or_none()
        return value

async def save_cached_country_info(country_key: str, country_query: str, answer: str):
    key = _normalize_country_key(country_key)
    Session = get_sessionmaker()
    async with Session() as session:
        stmt = (
            insert(CountryInfoCache)
            .values(country_key=key, country_query=country_query, answer=answer)
            .on_conflict_do_update(
                index_elements=[CountryInfoCache.country_key],
                set_={
                    "country_query": country_query,
                    "answer": answer,
                    "created_at": func.now(),
                },
            )
        )
        await session.execute(stmt)
        await session.execute(
            delete(CountryInfoCache).where(
                CountryInfoCache.created_at < (func.now() - text(f"INTERVAL '{COUNTRY_CACHE_TTL_DAYS} day'"))
            )
        )
        await session.commit()

async def delete_cached_country_info(country_key: str) -> None:
    key = _normalize_country_key(country_key)
    Session = get_sessionmaker()
    async with Session() as session:
        await session.execute(delete(CountryInfoCache).where(CountryInfoCache.country_key == key))
        await session.commit()

async def get_user_boost_until(tg_user_id: int) -> Optional[datetime]:
    Session = get_sessionmaker()
    async with Session() as session:
        stmt = select(User.boost_until).where(User.tg_user_id == tg_user_id)
        value = (await session.execute(stmt)).scalar_one_or_none()
        return value

async def add_boost_days(tg_user_id: int, days: int = 7):
    d = int(days)
    Session = get_sessionmaker()
    async with Session() as session:
        await session.execute(
            update(User)
            .where(User.tg_user_id == tg_user_id)
            .values(
                boost_until=func.greatest(func.coalesce(User.boost_until, func.now()), func.now())
                + text(f"INTERVAL '{d} day'"),
                updated_at=func.now(),
            )
        )
        await session.commit()

async def admin_get_stats() -> Dict[str, int]:
    Session = get_sessionmaker()
    async with Session() as session:
        total_users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        new_today = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(func.date(func.timezone("UTC", User.created_at)) == func.date(func.timezone("UTC", func.now())))
            )
        ).scalar_one()
        chat_today = (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.role == "user",
                    Message.mode == "chat",
                    func.date(func.timezone("UTC", Message.created_at)) == func.date(func.timezone("UTC", func.now())),
                )
            )
        ).scalar_one()
        country_today = (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.role == "user",
                    Message.mode == "country",
                    func.date(func.timezone("UTC", Message.created_at)) == func.date(func.timezone("UTC", func.now())),
                )
            )
        ).scalar_one()
        boosts_active = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.boost_until.is_not(None), User.boost_until > func.now())
            )
        ).scalar_one()
        cache_size = (await session.execute(select(func.count()).select_from(CountryInfoCache))).scalar_one()
    return {
        "total_users": int(total_users or 0),
        "new_today": int(new_today or 0),
        "chat_today": int(chat_today or 0),
        "country_today": int(country_today or 0),
        "boosts_active": int(boosts_active or 0),
        "cache_size": int(cache_size or 0),
    }

async def admin_get_user(tg_user_id: int) -> Optional[Dict]:
    return await get_user_profile(tg_user_id)

async def admin_find_users_by_username(query: str, limit: int = 10) -> List[Dict]:
    q = (query or "").strip().lstrip("@")
    if not q:
        return []
    Session = get_sessionmaker()
    async with Session() as session:
        stmt = (
            select(User.tg_user_id, User.username, User.first_name, User.last_name, User.created_at)
            .where(User.username.ilike(f"%{q}%"))
            .order_by(User.created_at.desc())
            .limit(int(limit))
        )
        rows = (await session.execute(stmt)).all()
    return [
        {"tg_user_id": r[0], "username": r[1], "first_name": r[2], "last_name": r[3], "created_at": r[4]}
        for r in rows
    ]

async def admin_get_user_today_counts(tg_user_id: int) -> Dict[str, int]:
    Session = get_sessionmaker()
    async with Session() as session:
        chat_used = (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.tg_user_id == tg_user_id,
                    Message.role == "user",
                    Message.mode == "chat",
                    func.date(func.timezone("UTC", Message.created_at)) == func.date(func.timezone("UTC", func.now())),
                )
            )
        ).scalar_one()

        country_used = (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.tg_user_id == tg_user_id,
                    Message.role == "user",
                    Message.mode == "country",
                    func.date(func.timezone("UTC", Message.created_at)) == func.date(func.timezone("UTC", func.now())),
                )
            )
        ).scalar_one()
    return {"chat": int(chat_used or 0), "country": int(country_used or 0)}

async def admin_clear_boost(tg_user_id: int):
    Session = get_sessionmaker()
    async with Session() as session:
        await session.execute(update(User).where(User.tg_user_id == tg_user_id).values(boost_until=None, updated_at=func.now()))
        await session.commit()

async def admin_list_cache(query: str, limit: int = 10) -> List[Dict]:
    q = (query or "").strip()
    Session = get_sessionmaker()
    async with Session() as session:
        stmt = select(CountryInfoCache.country_key, CountryInfoCache.country_query, CountryInfoCache.created_at)
        if q:
            stmt = stmt.where(
                CountryInfoCache.country_key.ilike(f"%{q}%") | CountryInfoCache.country_query.ilike(f"%{q}%")
            )
        stmt = stmt.order_by(CountryInfoCache.created_at.desc()).limit(int(limit))
        rows = (await session.execute(stmt)).all()
    return [{"country_key": r[0], "country_query": r[1], "created_at": r[2]} for r in rows]

async def admin_delete_cache(country_key: str):
    key = _normalize_country_key(country_key)
    Session = get_sessionmaker()
    async with Session() as session:
        await session.execute(delete(CountryInfoCache).where(CountryInfoCache.country_key == key))
        await session.commit()

async def admin_get_all_user_ids() -> List[int]:
    Session = get_sessionmaker()
    async with Session() as session:
        rows = (await session.execute(select(User.tg_user_id))).all()
    return [int(r[0]) for r in rows]
