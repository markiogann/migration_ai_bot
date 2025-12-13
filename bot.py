import asyncio
import inspect
import html
import re

from typing import Dict, Optional

from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    LabeledPrice,
    PreCheckoutQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatAction, ParseMode
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, ADMIN_IDS
from logic.ai import ask_llm
from logic.db import (
   init_db,
    close_db,
    ensure_user,
    save_message,
    get_user_profile,
    update_user_profile,
    get_recent_messages,
    get_cached_country_info,
    save_cached_country_info,
    delete_cached_country_info,
    get_daily_user_message_count,
    get_user_boost_until,
    add_boost_days,
    admin_get_stats,
    admin_get_user,
    admin_find_users_by_username,
    admin_get_user_today_counts,
    admin_clear_boost,
    admin_list_cache,
    admin_delete_cache,
    admin_get_all_user_ids,
)
from logic.texts_loader import msg, get_popular_countries, get_country_by_slug, reload_messages, reload_popular_countries

user_busy: Dict[int, bool] = {}
profile_state: Dict[int, str] = {}
user_mode: Dict[int, str] = {}
user_stage: Dict[int, str] = {}
admin_state: Dict[int, str] = {}
admin_tmp: Dict[int, Dict[str, str]] = {}


BTN_MENU_CHAT = "💬 Общение с ботом"
BTN_MENU_PROFILE = "📌 Мой профиль"
BTN_MENU_MODE = "⚙️ Выбор режима"
BTN_MENU_INFO_GENERAL = "🌍 Общая информация"
BTN_MENU_HELP = "📚 Справка"
BTN_HELP_BOT = "🤖 Как пользоваться ботом"
BTN_HELP_MIGRATION = "🌍 FAQ по переезду"
BTN_HELP_BACK = "◀️ Назад"
BTN_MENU_SUPPORT = "💳 Поддержать проект"
BTN_MENU_LIMITS = "📊 Лимиты"
CHAT_DAILY_LIMIT = 20
COUNTRY_DAILY_LIMIT = 10
CHAT_DAILY_LIMIT_BOOST = 30
COUNTRY_DAILY_LIMIT_BOOST = 20
BOOST_DAYS = 7
BTN_MENU_RESTART = "🔄 Перезапуск бота"

BTN_BACK_TO_MAIN = "В главное меню"

BTN_PROFILE_FILL = "Заполнить профиль"
BTN_PROFILE_FILL_AGAIN = "Заполнить профиль заново"
BTN_PROFILE_CLEAR = "Очистить профиль"

BTN_MODE_FREE_BASE = "Свободный режим"
BTN_MODE_PROFILE_BASE = "Режим с памятью профиля"

BTN_SKIP_QUESTION = "Пропустить этот вопрос"


async def call_llm(*args, **kwargs) -> str:
    def _call():
        return ask_llm(*args, **kwargs)

    result = await asyncio.to_thread(_call)
    if inspect.isawaitable(result):
        result = await result
    return str(result)


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MENU_CHAT), KeyboardButton(text=BTN_MENU_PROFILE)],
            [KeyboardButton(text=BTN_MENU_MODE), KeyboardButton(text=BTN_MENU_INFO_GENERAL)],
            [KeyboardButton(text=BTN_MENU_HELP), KeyboardButton(text=BTN_MENU_LIMITS)],
            [KeyboardButton(text=BTN_MENU_SUPPORT) ,KeyboardButton(text=BTN_MENU_RESTART)]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_chat_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_BACK_TO_MAIN)]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
async def send_long(message: types.Message, text: str, reply_markup=None):
    t = sanitize_telegram_html((text or "").strip())
    if not t:
        return
    limit = 3900
    first = True
    while len(t) > limit:
        cut = t.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        part = t[:cut].strip()
        if part:
            if first:
                await message.answer(part, reply_markup=reply_markup)
                first = False
            else:
                await message.answer(part)
        t = t[cut:].strip()
    if t:
        if first:
            await message.answer(t, reply_markup=reply_markup)
        else:
            await message.answer(t)

_ALLOWED_TAG_RE = re.compile(
    r'(?is)</?(b|i|u|s|code|pre)>|<a\s+href="[^"\n\r<>]+">|</a>'
)

def sanitize_telegram_html(text: str) -> str:
    if not text:
        return ""
    src = text
    tags = []

    def _stash(m: re.Match) -> str:
        tags.append(m.group(0))
        return f"\x00{len(tags)-1}\x00"

    tmp = _ALLOWED_TAG_RE.sub(_stash, src)
    tmp = html.escape(tmp, quote=False)

    def _restore(m: re.Match) -> str:
        idx = int(m.group(1))
        return tags[idx] if 0 <= idx < len(tags) else ""

    return re.sub(r"\x00(\d+)\x00", _restore, tmp)

def is_country_answer_cacheable(text: str) -> bool:
    if not text:
        return False

    low = text.strip().lower()
    if low.startswith("ошибка"):
        return False
    if "httpsconnectionpool" in low or "ssleoferror" in low or "unexpected_eof" in low or "traceback" in low:
        return False

    plain = re.sub(r"<[^>]+>", "", text)
    nums = re.findall(r"(?m)^\s*\d\.\s+", plain)
    if len(nums) >= 8:
        return True

    if len(plain.strip()) >= 800:
        return True

    return False

def get_help_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_HELP_BOT)],
            [KeyboardButton(text=BTN_HELP_MIGRATION)],
            [KeyboardButton(text=BTN_BACK_TO_MAIN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )

def get_skip_question_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SKIP_QUESTION)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def has_profile_data(profile: Optional[dict]) -> bool:
    if not profile:
        return False
    for key in ("home_country", "target_country", "migration_goal", "budget", "profession", "notes"):
        v = profile.get(key)
        if v:
            return True
    return False

def is_country_answer_cacheable(text: str) -> bool:
    if not text:
        return False

    low = text.strip().lower()
    bad = (
        "ошибка",
        "httpsconnectionpool",
        "ssleoferror",
        "unexpected_eof",
        "traceback",
        "max retries exceeded",
        "telegrambadrequest",
    )
    if any(x in low for x in bad):
        return False

    plain = re.sub(r"<[^>]+>", "", text)
    if len(plain.strip()) < 500:
        return False

    nums = re.findall(r"(?m)^\s*[1-8]\.\s+", plain)
    if len(nums) >= 6:
        return True

    markers = (
        "основные способы",
        "типы виз",
        "работ",
        "учеб",
        "стоимость",
        "официальн",
        "дисклеймер",
    )
    return sum(1 for m in markers if m in plain.lower()) >= 3


def make_profile_keyboard(profile: Optional[dict]) -> ReplyKeyboardMarkup:
    fill_text = BTN_PROFILE_FILL_AGAIN if has_profile_data(profile) else BTN_PROFILE_FILL
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=fill_text)],
            [KeyboardButton(text=BTN_PROFILE_CLEAR)],
            [KeyboardButton(text=BTN_BACK_TO_MAIN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def make_mode_keyboard(mode: str) -> ReplyKeyboardMarkup:
    if mode == "free":
        free_text = f"✅ {BTN_MODE_FREE_BASE}"
        prof_text = BTN_MODE_PROFILE_BASE
    else:
        free_text = BTN_MODE_FREE_BASE
        prof_text = f"✅ {BTN_MODE_PROFILE_BASE}"

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=free_text)],
            [KeyboardButton(text=prof_text)],
            [KeyboardButton(text=BTN_BACK_TO_MAIN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )

FAQ_BOT_TOPICS = [
    ("limits", "📊 Лимиты и поддержка", "faq_bot_limits"),
    ("profile", "📌 Профиль и режимы", "faq_bot_profile"),
    ("chat", "💬 Общение с ботом", "faq_bot_chat"),
    ("countries", "🌍 Раздел «Общая информация»", "faq_bot_countries"),
    ("privacy", "🔐 Безопасность", "faq_bot_privacy"),
]

FAQ_MIGRATION_TOPICS = [
    ("visa", "🛂 Что такое виза", "faq_mig_visa"),
    ("vnh_pmh", "🏠 ВНЖ vs ПМЖ", "faq_mig_vnh_pmh"),
    ("docs", "📄 Документы, переводы, апостиль", "faq_mig_docs"),
    ("money", "💶 Деньги и подтверждение средств", "faq_mig_money"),
    ("work", "👔 Работа и контракты", "faq_mig_work"),
    ("study", "🎓 Учёба и поступление", "faq_mig_study"),
    ("timeline", "⏳ Сроки и план переезда", "faq_mig_timeline"),
]

def build_faq_keyboard(prefix: str, topics: list[tuple[str, str, str]]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for slug, title, _msg_key in topics:
        row.append(InlineKeyboardButton(text=title, callback_data=f"{prefix}:{slug}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text=BTN_HELP_BACK, callback_data="help:root")])
    buttons.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="help:main")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
                InlineKeyboardButton(text="👤 Пользователь", callback_data="admin:user"),
            ],
            [
                InlineKeyboardButton(text="🌍 Кэш стран", callback_data="admin:cache"),
                InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast"),
            ],
            [
                InlineKeyboardButton(text="🔄 Reload текстов", callback_data="admin:reload"),
                InlineKeyboardButton(text="🏠 В меню", callback_data="admin:main"),
            ],
        ]
    )


def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:root")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="admin:main")],
        ]
    )


def admin_user_actions_kb(tg_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 +7 дней", callback_data=f"admin:boost:add7:{tg_user_id}"),
                InlineKeyboardButton(text="🚀 +30 дней", callback_data=f"admin:boost:add30:{tg_user_id}"),
            ],
            [InlineKeyboardButton(text="🧹 Убрать boost", callback_data=f"admin:boost:clear:{tg_user_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:root")],
        ]
    )

async def cmd_admin(message: types.Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("Нет доступа.")
        return
    admin_state.pop(user_id, None)
    admin_tmp.pop(user_id, None)
    await message.answer("Админ-панель:", reply_markup=admin_root_kb())

async def handle_admin_callback(callback: types.CallbackQuery):
    data = callback.data or ""
    user_id = callback.from_user.id

    await callback.answer()

    if not is_admin(user_id):
        if callback.message:
            await callback.message.answer("Нет доступа.")
        return

    if not callback.message:
        return

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "root":
        admin_state.pop(user_id, None)
        admin_tmp.pop(user_id, None)
        await callback.message.answer("Админ-панель:", reply_markup=admin_root_kb())
        return

    if action == "main":
        admin_state.pop(user_id, None)
        admin_tmp.pop(user_id, None)
        await show_main_menu(callback.message, user_id)
        return

    if action == "stats":
        s = await admin_get_stats()
        text = (
            "Статистика:\n\n"
            f"👥 Пользователей всего: {s['total_users']}\n"
            f"🆕 Новых сегодня: {s['new_today']}\n"
            f"💬 Сообщений сегодня (chat): {s['chat_today']}\n"
            f"🌍 Сообщений сегодня (country): {s['country_today']}\n"
            f"🚀 Активных boost: {s['boosts_active']}\n"
            f"🗃 Кэш стран: {s['cache_size']}"
        )
        await callback.message.answer(text, reply_markup=admin_back_kb())
        return

    if action == "user":
        admin_state[user_id] = "await_user_query"
        await callback.message.answer("Введи tg_user_id или @username:", reply_markup=admin_back_kb())
        return

    if action == "cache":
        admin_state[user_id] = "await_cache_query"
        await callback.message.answer("Введи ключ/название для поиска в кэше (или отправь '-' для последних):", reply_markup=admin_back_kb())
        return

    if action == "broadcast":
        admin_state[user_id] = "await_broadcast_text"
        await callback.message.answer("Отправь текст рассылки:", reply_markup=admin_back_kb())
        return

    if action == "reload":
        reload_messages()
        reload_popular_countries()
        await callback.message.answer("Перезагружено.", reply_markup=admin_back_kb())
        return

    if action == "boost" and len(parts) >= 4:
        sub = parts[2]
        target_id = int(parts[3])

        if sub == "add7":
            await add_boost_days(target_id, 7)
            u = await admin_get_user(target_id)
            bu = u.get("boost_until") if u else None
            await callback.message.answer(f"Готово. boost_until: {bu}", reply_markup=admin_user_actions_kb(target_id))
            return

        if sub == "add30":
            await add_boost_days(target_id, 30)
            u = await admin_get_user(target_id)
            bu = u.get("boost_until") if u else None
            await callback.message.answer(f"Готово. boost_until: {bu}", reply_markup=admin_user_actions_kb(target_id))
            return

        if sub == "clear":
            await admin_clear_boost(target_id)
            await callback.message.answer("Boost убран.", reply_markup=admin_user_actions_kb(target_id))
            return

    if action == "cache_del" and len(parts) >= 3:
        key = parts[2]
        await admin_delete_cache(key)
        await callback.message.answer("Удалено.", reply_markup=admin_back_kb())
        return

async def handle_admin_input(message: types.Message, state: str):
    user_id = message.from_user.id
    text = (message.text or "").strip()

    if state == "await_user_query":
        admin_state.pop(user_id, None)

        if text.isdigit():
            tid = int(text)
            u = await admin_get_user(tid)
            if not u:
                await message.answer("Пользователь не найден.", reply_markup=admin_back_kb())
                return

            counts = await admin_get_user_today_counts(tid)
            bu = u.get("boost_until")

            info = (
                "Пользователь:\n\n"
                f"tg_user_id: {u.get('tg_user_id')}\n"
                f"username: {u.get('username')}\n"
                f"first_name: {u.get('first_name')}\n"
                f"last_name: {u.get('last_name')}\n"
                f"home_country: {u.get('home_country')}\n"
                f"target_country: {u.get('target_country')}\n"
                f"migration_goal: {u.get('migration_goal')}\n"
                f"budget: {u.get('budget')}\n"
                f"profession: {u.get('profession')}\n"
                f"notes: {u.get('notes')}\n\n"
                f"today chat: {counts['chat']}\n"
                f"today country: {counts['country']}\n"
                f"boost_until: {bu}"
            )
            await message.answer(info, reply_markup=admin_user_actions_kb(tid))
            return

        q = text.lstrip("@")
        res = await admin_find_users_by_username(q, limit=10)
        if not res:
            await message.answer("Не найдено.", reply_markup=admin_back_kb())
            return

        lines = ["Найдено:"]
        for r in res:
            lines.append(f"{r.get('tg_user_id')}  @{r.get('username')}  {r.get('first_name') or ''} {r.get('last_name') or ''}".strip())
        await message.answer("\n".join(lines), reply_markup=admin_back_kb())
        return

    if state == "await_cache_query":
        admin_state.pop(user_id, None)

        q = "" if text == "-" else text
        items = await admin_list_cache(q, limit=10)
        if not items:
            await message.answer("Пусто.", reply_markup=admin_back_kb())
            return

        rows = []
        kb_rows = []
        for it in items:
            ck = it.get("country_key")
            cq = it.get("country_query")
            rows.append(f"{ck} — {cq}")
            kb_rows.append([InlineKeyboardButton(text=f"🗑 {ck}", callback_data=f"admin:cache_del:{ck}")])

        kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:root")])
        kb_rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="admin:main")])

        await message.answer("\n".join(rows), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
        return

    if state == "await_broadcast_text":
        admin_state.pop(user_id, None)

        if not text:
            await message.answer("Пустой текст.", reply_markup=admin_back_kb())
            return

        ids = await admin_get_all_user_ids()
        sent = 0
        failed = 0

        for uid in ids:
            try:
                await message.bot.send_message(chat_id=uid, text=text)
                sent += 1
            except Exception:
                failed += 1

        await message.answer(f"Рассылка завершена. sent={sent}, failed={failed}", reply_markup=admin_back_kb())
        return


async def show_limits_screen(message: types.Message, user_id: int):
    chat_limit, country_limit, boost_until = await get_effective_limits(user_id)

    try:
        chat_used = await get_daily_user_message_count(user_id, "chat")
        country_used = await get_daily_user_message_count(user_id, "country")
    except Exception as e:
        print("[BOT] get_daily_user_message_count error:", repr(e))
        chat_used = 0
        country_used = 0

    chat_left = max(0, chat_limit - chat_used)
    country_left = max(0, country_limit - country_used)

    boost_line = ""
    now = datetime.now(timezone.utc)
    if boost_until:
        bu = boost_until if boost_until.tzinfo else boost_until.replace(tzinfo=timezone.utc)
        if bu > now:
            boost_line = f"\n\n🚀 Повышенные лимиты активны до: {bu.strftime('%Y-%m-%d %H:%M UTC')}"

    text = (
        "Лимиты на сегодня:\n\n"
        f"💬 Чат: {chat_used}/{chat_limit} (осталось {chat_left})\n"
        f"🌍 Страны: {country_used}/{country_limit} (осталось {country_left})"
        f"{boost_line}\n\n"
        "Чтобы увеличить лимиты — нажмите «💳 Поддержать проект»."
    )
    await message.answer(text, reply_markup=get_main_menu_keyboard())


async def get_effective_limits(user_id: int) -> tuple[int, int, Optional[datetime]]:
    try:
        boost_until = await get_user_boost_until(user_id)
    except Exception as e:
        print("[BOT] get_user_boost_until error:", repr(e))
        boost_until = None

    now = datetime.now(timezone.utc)
    if boost_until and boost_until.tzinfo is None:
        boost_until = boost_until.replace(tzinfo=timezone.utc)

    boosted = bool(boost_until and boost_until > now)
    chat_limit = CHAT_DAILY_LIMIT_BOOST if boosted else CHAT_DAILY_LIMIT
    country_limit = COUNTRY_DAILY_LIMIT_BOOST if boosted else COUNTRY_DAILY_LIMIT
    return chat_limit, country_limit, boost_until


async def show_main_menu(message: types.Message, user_id: int):
    user_stage[user_id] = "menu"
    await message.answer(msg("main_menu"), reply_markup=get_main_menu_keyboard())


async def show_profile_screen(message: types.Message, user_id: int):
    profile = await get_user_profile(user_id)

    if not profile:
        await ensure_user(
            tg_user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
        )
        profile = await get_user_profile(user_id)

    def val(key: str) -> str:
        v = profile.get(key) if profile else None
        return v if v else "не указано"

    text = (
        "Мой профиль:\n\n"
        f"- страна проживания: {val('home_country')}\n"
        f"- страна, куда хотите переехать: {val('target_country')}\n"
        f"- цель переезда: {val('migration_goal')}\n"
        f"- бюджет: {val('budget')}\n"
        f"- профессия/сфера: {val('profession')}\n"
        f"- заметки: {val('notes')}\n\n"
        "Выберите действие:"
    )

    await message.answer(text, reply_markup=make_profile_keyboard(profile))


async def show_mode_screen(message: types.Message, user_id: int):
    mode = user_mode.get(user_id, "profile")
    text = (
        "Выбор режима работы бота:\n\n"
        "- Свободный режим — бот отвечает на вопросы, не учитывая профиль.\n"
        "- Режим с памятью профиля — бот учитывает сохранённые данные "
        "о вашей ситуации (страна, цель переезда, бюджет и т.д.)."
    )
    await message.answer(text, reply_markup=make_mode_keyboard(mode))


async def cmd_start(message: types.Message):
    user = message.from_user
    user_id = user.id

    await ensure_user(
        tg_user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
    )

    user_mode[user_id] = user_mode.get(user_id, "profile")
    user_stage[user_id] = "menu"
    profile_state.pop(user_id, None)

    await message.answer(msg("welcome"), reply_markup=get_main_menu_keyboard())


async def cmd_help(message: types.Message):
    await message.answer(msg("help"))


async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    user_stage[user_id] = "menu"
    await show_profile_screen(message, user_id)


async def handle_pre_checkout_query(pre_checkout_query: PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(
        pre_checkout_query_id=pre_checkout_query.id,
        ok=True,
    )


async def handle_successful_payment(message: types.Message):
    payment = message.successful_payment

    if payment and payment.currency == "XTR" and (payment.invoice_payload or "").startswith("donation_stars"):
        user = message.from_user
        if user:
            try:
                await ensure_user(
                    tg_user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    language_code=user.language_code,
                )
            except Exception as e:
                print("[BOT] ensure_user (payment) error:", repr(e))

        try:
            await add_boost_days(message.from_user.id, BOOST_DAYS)
        except Exception as e:
            print("[BOT] add_boost_days error:", repr(e))

        await message.answer(msg("donation_thanks"))
        await message.answer(f"🚀 Повышенные лимиты активированы на {BOOST_DAYS} дней.")
    else:
        await message.answer(msg("donation_generic"))
        
def build_popular_countries_keyboard() -> Optional[InlineKeyboardMarkup]:
    popular = get_popular_countries()
    if not popular:
        return None

    buttons, row = [], []
    for slug, cfg in popular.items():
        row.append(
            InlineKeyboardButton(
                text=cfg.get("display_name", slug),
                callback_data=f"country:{slug}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_country_again_prompt(message: types.Message):
    kb = build_popular_countries_keyboard()
    if kb:
        await message.answer(
            "Хотите узнать про другую страну? Выберите ниже или введите название вручную.",
            reply_markup=kb,
        )


async def process_country_request(message: types.Message, user: types.User, country_query: str):
    user_id = user.id
    country_query = (country_query or "").strip()

    if not country_query:
        await message.answer(
            "Пожалуйста, введите название страны, например: Франция, Германия, Канада.",
            reply_markup=get_chat_keyboard(),
        )
        return

    if user_busy.get(user_id):
        await message.answer("Я ещё отвечаю на ваш предыдущий запрос. Подождите, пожалуйста 🙌")
        return

    if not is_admin(user_id):
        _, country_limit, _ = await get_effective_limits(user_id)

        try:
            used_today = await get_daily_user_message_count(user_id, "country")
        except Exception as e:
            print("[BOT] get_daily_user_message_count(country) error:", repr(e))
            used_today = 0

        if used_today >= country_limit:
            await message.answer(
                f"Лимит справок по странам на сегодня исчерпан ({country_limit}).\n\n"
                "Попробуйте завтра или нажмите «💳 Поддержать проект».",
                reply_markup=get_chat_keyboard(),
            )
            return


    try:
        await save_message(user_id, "user", country_query, mode=("admin" if is_admin(user_id) else "country"))
    except Exception as e:
        print("[BOT] save_message(user, country) error:", repr(e))

    user_busy[user_id] = True
    thinking_msg: Optional[types.Message] = None

    try:
        country_key = country_query.lower()

        try:
            cached = await get_cached_country_info(country_key)
        except Exception as e:
            print("[BOT] get_cached_country_info error:", repr(e))
            cached = None

        if cached and is_country_answer_cacheable(cached):
            await send_long(message, cached, reply_markup=get_chat_keyboard())
            await send_country_again_prompt(message)
            return

        if cached and not is_country_answer_cacheable(cached):
            try:
                await delete_cached_country_info(country_key)
            except Exception as e:
                print("[BOT] delete_cached_country_info error:", repr(e))


        try:
            await ensure_user(
                tg_user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
            )
        except Exception as e:
            print("[BOT] ensure_user (country) error:", repr(e))

        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        thinking_msg = await message.answer("⏳ Собираю информацию по стране...")

        answer = await call_llm(country_query, mode="country", profile=None, history=None)
        
        if answer.strip().lower().startswith("ошибка"):
            answer = "Сейчас не удалось получить справку по стране из-за временной сетевой ошибки. Попробуйте ещё раз через минуту."

        if is_country_answer_cacheable(answer):
            try:
                await save_cached_country_info(
                    country_key=country_key,
                    country_query=country_query,
                    answer=answer,
                )
            except Exception as e:
                print("[BOT] save_cached_country_info error:", repr(e))


        if thinking_msg:
            try:
                await thinking_msg.delete()
            except Exception as e:
                print("[BOT] delete thinking_msg (country) error:", repr(e))

        await send_long(message, answer, reply_markup=get_chat_keyboard())
        await send_country_again_prompt(message)

    finally:
        user_busy[user_id] = False

async def handle_country_info_message(message: types.Message):
    await process_country_request(message, message.from_user, message.text or "")

async def handle_help_callback(callback: types.CallbackQuery):
    data = callback.data or ""
    await callback.answer()

    if not callback.message:
        return

    user_id = callback.from_user.id

    if data == "help:root":
        user_stage[user_id] = "help"
        await callback.message.answer(
            msg("help_root", "📚 Справка\n\nВыберите раздел:"),
            reply_markup=get_help_menu_keyboard(),
        )
        return

    if data == "help:main":
        await show_main_menu(callback.message, user_id)
        return

    if data.startswith("faqb:"):
        slug = data.split(":", 1)[1]
        topic = next((t for t in FAQ_BOT_TOPICS if t[0] == slug), None)
        if not topic:
            await callback.message.answer("Тема не найдена.")
            return

        _slug, _title, msg_key = topic
        text = msg(msg_key, "Пока нет текста для этой темы.")
        await callback.message.answer(text)
        return


    if data.startswith("faqm:"):
        slug = data.split(":", 1)[1]
        topic = next((t for t in FAQ_MIGRATION_TOPICS if t[0] == slug), None)
        if not topic:
            await callback.message.answer("Тема не найдена.")
            return

        _slug, _title, msg_key = topic
        text = msg(msg_key, "Пока нет текста для этой темы.")
        await callback.message.answer(text)
        return

async def handle_country_button(callback: types.CallbackQuery):
    data = callback.data or ""
    if not data.startswith("country:"):
        await callback.answer()
        return

    slug = data.split(":", 1)[1]
    cfg = get_country_by_slug(slug)
    if not cfg:
        await callback.answer("Это направление пока недоступно.", show_alert=True)
        return

    await callback.answer()

    country_query = (cfg.get("country_query") or cfg.get("display_name") or slug).strip()

    if not callback.message:
        return

    await process_country_request(callback.message, callback.from_user, country_query)


async def handle_profile_answer(message: types.Message, state: str):
    user_id = message.from_user.id
    text = (message.text or "").strip()

    async def set_field(field_name: str):
        value = None if text == BTN_SKIP_QUESTION else text
        await update_user_profile(user_id, **{field_name: value})

    if state == "home_country":
        await set_field("home_country")
        profile_state[user_id] = "target_country"
        await message.answer(
            "🌍 В какую страну вы планируете переезд (или рассматриваете варианты)?",
            reply_markup=get_skip_question_keyboard(),
        )
        return

    if state == "target_country":
        await set_field("target_country")
        profile_state[user_id] = "migration_goal"
        await message.answer(
            "Какова основная цель переезда? (работа, учёба, воссоединение семьи, ПМЖ и т.п.)",
            reply_markup=get_skip_question_keyboard(),
        )
        return

    if state == "migration_goal":
        await set_field("migration_goal")
        profile_state[user_id] = "budget"
        await message.answer(
            "Какой у вас примерный бюджет/уровень дохода для жизни за рубежом?",
            reply_markup=get_skip_question_keyboard(),
        )
        return

    if state == "budget":
        await set_field("budget")
        profile_state[user_id] = "profession"
        await message.answer(
            "Кто вы по профессии или в какой сфере работаете/учитесь?",
            reply_markup=get_skip_question_keyboard(),
        )
        return

    if state == "profession":
        await set_field("profession")
        profile_state[user_id] = "notes"
        await message.answer(
            "Есть ли какие-то дополнительные важные детали? "
            "(семья, язык, наличие виз, ограничения и т.п.)\n\n"
            "Если ничего важного нет — нажмите «Пропустить этот вопрос».",
            reply_markup=get_skip_question_keyboard(),
        )
        return

    if state == "notes":
        await set_field("notes")
        profile_state.pop(user_id, None)

        await message.answer(
            "Спасибо! Профиль обновлён ✅",
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_profile_screen(message, user_id)
        return

    profile_state.pop(user_id, None)
    await show_profile_screen(message, user_id)


async def handle_menu_buttons(message: types.Message):
    user_id = message.from_user.id
    text = (message.text or "").strip()
    stage = user_stage.get(user_id, "menu")

    normalized = text.replace("✅", "").strip()

    if normalized == BTN_MENU_CHAT:
        user_stage[user_id] = "chat"
        await message.answer(msg("chat_intro"), reply_markup=get_chat_keyboard())
        return

    if normalized == BTN_MENU_PROFILE:
        user_stage[user_id] = "menu"
        await show_profile_screen(message, user_id)
        return

    if normalized == BTN_MENU_MODE:
        user_stage[user_id] = "menu"
        await show_mode_screen(message, user_id)
        return

    if normalized == BTN_MENU_INFO_GENERAL:
        user_stage[user_id] = "country_info"

        intro = msg(
            "country_info_intro",
            "Раздел общей информации.\n\n"
            "Введите название страны, по которой хотите получить краткую миграционную справку, "
            "или выберите одну из популярных ниже.\n\n"
            "Популярные направления:"
        )

        kb = build_popular_countries_keyboard()
        if kb:
            await message.answer(intro, reply_markup=kb)
        else:
            await message.answer(intro, reply_markup=get_chat_keyboard())
        return

    if normalized == BTN_MENU_HELP:
        user_stage[user_id] = "help"
        await message.answer(
            msg("help_root", "📚 Справка\n\nВыберите раздел:"),
            reply_markup=get_help_menu_keyboard(),
        )
        return
   
    if normalized == BTN_HELP_BOT:
        user_stage[user_id] = "help_bot"
        await message.answer(
            msg("help_bot_intro", "🤖 Как пользоваться ботом\n\nВыберите тему:"),
            reply_markup=build_faq_keyboard("faqb", FAQ_BOT_TOPICS),
        )
        return

    if normalized == BTN_HELP_MIGRATION:
        user_stage[user_id] = "help_mig"
        await message.answer(
            msg("help_mig_intro", "🌍 FAQ по переезду (общие вопросы)\n\nВыберите тему:"),
            reply_markup=build_faq_keyboard("faqm", FAQ_MIGRATION_TOPICS),
        )
        return


    if normalized == BTN_MENU_LIMITS:
        user_stage[user_id] = "menu"
        await show_limits_screen(message, user_id)
        return

    if normalized == BTN_MENU_SUPPORT:
        user_stage[user_id] = "menu"

        prices = [
            LabeledPrice(
                label="Поддержать проект",
                amount=50,
            )
        ]

        await message.answer_invoice(
            title="Поддержать проект",
            description="Добровольный донат для развития миграционного ИИ-ассистента по миграции.",
            payload="donation_stars_50",
            currency="XTR",
            prices=prices,
            provider_token="",
        )
        return

    if normalized == BTN_MENU_RESTART:
        await cmd_start(message)
        return

    if normalized == BTN_BACK_TO_MAIN:
        await show_main_menu(message, user_id)
        return

    if normalized == BTN_MODE_FREE_BASE:
        user_mode[user_id] = "free"
        await message.answer(
            "Включён свободный режим: я не учитываю сохранённый профиль, "
            "но всё равно отвечаю только на вопросы по миграции.",
            reply_markup=make_mode_keyboard("free"),
        )
        return

    if normalized == BTN_MODE_PROFILE_BASE:
        user_mode[user_id] = "profile"
        profile = await get_user_profile(user_id)
        if not has_profile_data(profile):
            warning = (
                "Включён режим с памятью профиля, но ваш профиль пока почти пуст.\n"
                "Рекомендую заполнить его в разделе «Мой профиль»."
            )
        else:
            warning = "Включён режим с памятью профиля. Я буду учитывать ваши данные при ответах."
        await message.answer(warning, reply_markup=make_mode_keyboard("profile"))
        return

    if normalized in (BTN_PROFILE_FILL, BTN_PROFILE_FILL_AGAIN):
        profile_state[user_id] = "home_country"
        await message.answer(
            "Заполним профиль.\n\n👤 В какой стране вы сейчас живёте?",
            reply_markup=get_skip_question_keyboard(),
        )
        return

    if normalized == BTN_PROFILE_CLEAR:
        await update_user_profile(
            user_id,
            home_country=None,
            target_country=None,
            migration_goal=None,
            budget=None,
            profession=None,
            notes=None,
        )
        await message.answer("Профиль очищен.", reply_markup=ReplyKeyboardRemove())
        await show_profile_screen(message, user_id)
        return

    if stage != "chat":
        await show_main_menu(message, user_id)


async def echo_message(message: types.Message):
    user = message.from_user
    user_id = user.id
    if is_admin(user_id):
        st = admin_state.get(user_id)
        if st:
            await handle_admin_input(message, st)
            return

    user_text = (message.text or "").strip()

    state = profile_state.get(user_id)
    if state:
        await handle_profile_answer(message, state)
        return

    stage = user_stage.get(user_id, "menu")

    if stage == "country_info":
        await handle_country_info_message(message)
        return

    if stage != "chat":
        await message.answer(msg("menu_use_hint"), reply_markup=get_main_menu_keyboard())
        return
    
    if not is_admin(user_id):
        chat_limit, _, _ = await get_effective_limits(user_id)

        try:
            used_today = await get_daily_user_message_count(user_id, "chat")
        except Exception as e:
            print("[BOT] get_daily_user_message_count(chat) error:", repr(e))
            used_today = 0

        if used_today >= chat_limit:
            await message.answer(
                f"Лимит сообщений в чате на сегодня исчерпан ({chat_limit}).\n\n"
                "Попробуйте завтра или нажмите «💳 Поддержать проект».",
                reply_markup=get_chat_keyboard(),
            )
            return



    if user_busy.get(user_id):
        await message.answer("Я ещё отвечаю на ваш предыдущий вопрос. Подождите, пожалуйста 🙌")
        return

    user_busy[user_id] = True
    thinking_msg: Optional[types.Message] = None

    try:
        try:
            await ensure_user(
                tg_user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
            )
        except Exception as e:
            print("[BOT] ensure_user error:", repr(e))

        try:
            await save_message(user.id, "user", user_text, mode=("admin" if is_admin(user_id) else "chat"))
        except Exception as e:
            print("[BOT] save_message(user) error:", repr(e))

        mode = user_mode.get(user_id, "profile")
        profile = await get_user_profile(user.id) if mode == "profile" else None
        history = await get_recent_messages(user.id, limit=6)

        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        thinking_msg = await message.answer(msg("thinking_chat"))

        answer = await call_llm(user_text, "chat", profile=profile, history=history)

        try:
            await save_message(user.id, "assistant", answer)
        except Exception as e:
            print("[BOT] save_message(assistant) error:", repr(e))

        if thinking_msg:
            try:
                await thinking_msg.delete()
            except Exception as e:
                print("[BOT] delete thinking_msg (chat) error:", repr(e))

        await send_long(message, answer, reply_markup=get_chat_keyboard())

    finally:
        user_busy[user_id] = False


async def main():
    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command(commands=["help"]))
    dp.message.register(cmd_profile, Command(commands=["profile"]))
    dp.message.register(cmd_admin, Command(commands=["admin"]))
    dp.callback_query.register(handle_admin_callback, F.data.startswith("admin:"))
    dp.pre_checkout_query.register(handle_pre_checkout_query)
    dp.message.register(handle_successful_payment, F.successful_payment)

    dp.callback_query.register(handle_country_button, F.data.startswith("country:"))
    
    dp.callback_query.register(
        handle_help_callback,
        F.data.startswith("help:") | F.data.startswith("faqb:") | F.data.startswith("faqm:")
    )

    dp.message.register(
        handle_menu_buttons,
        F.text.in_([
            BTN_MENU_CHAT,
            BTN_MENU_PROFILE,
            BTN_MENU_MODE,
            BTN_MENU_INFO_GENERAL,
            BTN_MENU_LIMITS,
            BTN_MENU_SUPPORT,
            BTN_MENU_HELP,
            BTN_HELP_BOT,
            BTN_HELP_MIGRATION,
            BTN_MENU_RESTART,
            BTN_BACK_TO_MAIN,
            BTN_PROFILE_FILL,
            BTN_PROFILE_FILL_AGAIN,
            BTN_PROFILE_CLEAR,
            BTN_MODE_FREE_BASE,
            f"✅ {BTN_MODE_FREE_BASE}",
            BTN_MODE_PROFILE_BASE,
            f"✅ {BTN_MODE_PROFILE_BASE}",
        ]),
    )

    dp.message.register(echo_message, F.text)

    print("Бот запущен. Нажми Ctrl+C для остановки.")
    try:
        await dp.start_polling(bot)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
