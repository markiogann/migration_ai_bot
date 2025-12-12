import asyncio
import inspect
from typing import Dict, Optional

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
from aiogram.enums import ChatAction

from config import BOT_TOKEN
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
)
from logic.texts_loader import msg, get_popular_countries, get_country_by_slug


user_busy: Dict[int, bool] = {}
profile_state: Dict[int, str] = {}
user_mode: Dict[int, str] = {}
user_stage: Dict[int, str] = {}


BTN_MENU_CHAT = "💬 Общение с ботом"
BTN_MENU_PROFILE = "📌 Мой профиль"
BTN_MENU_MODE = "⚙️ Выбор режима"
BTN_MENU_INFO_GENERAL = "🌍 Общая информация"
BTN_MENU_INFO_BOT = "ℹ️ О боте"
BTN_MENU_SUPPORT = "💳 Поддержать проект"
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
            [
                KeyboardButton(text=BTN_MENU_CHAT),
                KeyboardButton(text=BTN_MENU_PROFILE),
            ],
            [
                KeyboardButton(text=BTN_MENU_MODE),
                KeyboardButton(text=BTN_MENU_INFO_GENERAL),
            ],
            [
                KeyboardButton(text=BTN_MENU_INFO_BOT),
                KeyboardButton(text=BTN_MENU_SUPPORT),
            ],
            [
                KeyboardButton(text=BTN_MENU_RESTART),
            ],
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
    if payment and payment.currency == "XTR" and payment.invoice_payload.startswith("donation_stars"):
        await message.answer(msg("donation_thanks"))
    else:
        await message.answer(msg("donation_generic"))


async def handle_country_info_message(message: types.Message):
    user = message.from_user
    user_id = user.id
    country_query = (message.text or "").strip()

    if not country_query:
        await message.answer(
            "Пожалуйста, введите название страны, например: Франция, Германия, Канада.",
            reply_markup=get_chat_keyboard(),
        )
        return

    if user_busy.get(user_id):
        await message.answer("Я ещё отвечаю на ваш предыдущий запрос. Подождите, пожалуйста 🙌")
        return

    user_busy[user_id] = True
    thinking_msg: types.Message | None = None

    try:
        country_key = country_query.lower()

        try:
            cached = await get_cached_country_info(country_key)
        except Exception as e:
            print("[BOT] get_cached_country_info error:", repr(e))
            cached = None

        if cached:
            await message.answer(cached, reply_markup=get_chat_keyboard())
            return

        try:
            await ensure_user(
                tg_user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
            )
        except Exception as e:
            print("[BOT] ensure_user (country_info) error:", repr(e))

        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        thinking_msg = await message.answer("⏳ Собираю информацию по стране...")

        answer = await call_llm(country_query, mode="country", profile=None, history=None)

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
                print("[BOT] delete thinking_msg (country_info) error:", repr(e))

        await message.answer(answer, reply_markup=get_chat_keyboard())

    finally:
        user_busy[user_id] = False


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

    user = callback.from_user
    user_id = user.id

    country_query = (cfg.get("country_query") or cfg.get("display_name") or slug).strip()
    country_key = country_query.lower()

    if user_busy.get(user_id):
        await callback.answer("Я ещё отвечаю на ваш предыдущий запрос.", show_alert=True)
        return

    user_busy[user_id] = True
    thinking_msg: types.Message | None = None

    try:
        await callback.answer()

        try:
            cached = await get_cached_country_info(country_key)
        except Exception as e:
            print("[BOT] get_cached_country_info (button) error:", repr(e))
            cached = None

        if cached:
            await callback.message.answer(cached, reply_markup=get_chat_keyboard())
            return

        try:
            await ensure_user(
                tg_user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
            )
        except Exception as e:
            print("[BOT] ensure_user (country_button) error:", repr(e))

        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
        thinking_msg = await callback.message.answer("⏳ Собираю информацию по стране...")

        answer = await call_llm(country_query, mode="country", profile=None, history=None)

        try:
            await save_cached_country_info(
                country_key=country_key,
                country_query=country_query,
                answer=answer,
            )
        except Exception as e:
            print("[BOT] save_cached_country_info (button) error:", repr(e))

        if thinking_msg:
            try:
                await thinking_msg.delete()
            except Exception as e:
                print("[BOT] delete thinking_msg (country_button) error:", repr(e))

        await callback.message.answer(answer, reply_markup=get_chat_keyboard())

    finally:
        user_busy[user_id] = False


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

        text = msg(
            "country_info_intro",
            "Раздел общей информации.\n\n"
            "Введите название страны, по которой хотите получить краткую миграционную справку, "
            "или выберите одну из популярных ниже.\n\n"
            "Популярные направления:"
        )

        popular = get_popular_countries()

        if popular:
            buttons = []
            row = []
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

            inline_kb = InlineKeyboardMarkup(inline_keyboard=buttons)
            await message.answer(text, reply_markup=inline_kb)
        else:
            await message.answer(text, reply_markup=get_chat_keyboard())

        return

    if normalized == BTN_MENU_INFO_BOT:
        user_stage[user_id] = "menu"
        await message.answer(msg("about_bot"), reply_markup=get_main_menu_keyboard())
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
            await save_message(user.id, "user", user_text)
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

        await message.answer(answer, reply_markup=get_chat_keyboard())

    finally:
        user_busy[user_id] = False


async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command(commands=["help"]))
    dp.message.register(cmd_profile, Command(commands=["profile"]))

    dp.pre_checkout_query.register(handle_pre_checkout_query)
    dp.message.register(handle_successful_payment, F.successful_payment)

    dp.callback_query.register(handle_country_button, F.data.startswith("country:"))

    dp.message.register(
        handle_menu_buttons,
        F.text.in_([
            BTN_MENU_CHAT,
            BTN_MENU_PROFILE,
            BTN_MENU_MODE,
            BTN_MENU_INFO_GENERAL,
            BTN_MENU_INFO_BOT,
            BTN_MENU_SUPPORT,
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
