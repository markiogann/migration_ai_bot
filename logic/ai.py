import os
import re
import json
from typing import Optional, Dict, Any, List, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from openai import OpenAI

from logic.prompts import MIGRATION_ASSISTANT_SYSTEM_PROMPT
from logic.prompts_country_info import COUNTRY_INFO_PROMPT

load_dotenv()

PPLX_API_KEY = os.getenv("PPLX_API_KEY")
PPLX_URL = "https://api.perplexity.ai/chat/completions"
PPLX_MODEL = os.getenv("PPLX_MODEL", "sonar")

HISTORY_ITEM_MAX_CHARS = int(os.getenv("HISTORY_ITEM_MAX_CHARS", "800"))
HISTORY_TOTAL_MAX_CHARS = int(os.getenv("HISTORY_TOTAL_MAX_CHARS", "3000"))

USER_MESSAGE_MAX_CHARS = int(os.getenv("USER_MESSAGE_MAX_CHARS", "2000"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_URL = os.getenv("OPENAI_URL", "https://api.openai.com/v1/responses")
_openai_client: Optional[OpenAI] = None

def _get_openai_client() -> Optional[OpenAI]:
    global _openai_client
    if not OPENAI_API_KEY:
        return None
    if _openai_client is None:
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client

DOMAIN_GATE_ENABLED = (os.getenv("DOMAIN_GATE_ENABLED", "1").strip() == "1")
DOMAIN_GATE_MODEL = os.getenv("DOMAIN_GATE_MODEL", OPENAI_MODEL)

_session = requests.Session()

_retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.7,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["POST"]),
    raise_on_status=False,
)

_adapter = HTTPAdapter(max_retries=_retry)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def _cleanup_text(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    cleaned = re.sub(r"\s*\[\d+\]", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]+([,.!?])", r"\1", cleaned)
    return cleaned.strip()


def _build_profile_context(profile: Optional[Dict[str, Any]]) -> str:
    if not profile:
        return ""
    parts: List[str] = []
    if profile.get("home_country"):
        parts.append(f"- страна проживания: {profile['home_country']}")
    if profile.get("target_country"):
        parts.append(f"- страна, куда хочет переехать: {profile['target_country']}")
    if profile.get("migration_goal"):
        parts.append(f"- цель переезда: {profile['migration_goal']}")
    if profile.get("budget"):
        parts.append(f"- примерный бюджет: {profile['budget']}")
    if profile.get("profession"):
        parts.append(f"- профессия/сфера: {profile['profession']}")
    if profile.get("notes"):
        parts.append(f"- дополнительные заметки: {profile['notes']}")
    if not parts:
        return ""
    return "Профиль пользователя:\n" + "\n".join(parts)


def _build_history_context(history: Optional[List[Dict[str, Any]]]) -> str:
    if not history:
        return ""

    lines: List[str] = []
    total = 0

    for m in history:
        role = m.get("role")
        text = (m.get("text") or "").strip()
        if not text:
            continue

        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if len(text) > HISTORY_ITEM_MAX_CHARS:
            text = text[:HISTORY_ITEM_MAX_CHARS].rstrip()

        prefix = "Пользователь" if role == "user" else "Ассистент"
        line = f"{prefix}: {text}"

        if total + len(line) + 1 > HISTORY_TOTAL_MAX_CHARS:
            break

        lines.append(line)
        total += len(line) + 1

    if not lines:
        return ""
    return "Краткая история (старые → новые):\n" + "\n".join(lines)


def _extract_json(text: str) -> Optional[str]:
    if not text:
        return None
    s = text.strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        return None
    return m.group(0).strip()

def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    j = _extract_json(text)
    if not j:
        return None
    try:
        obj = json.loads(j)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

def _normalize_sources(sources: Any) -> List[str]:
    if not sources or not isinstance(sources, list):
        return []
    out: List[str] = []
    for x in sources:
        if not isinstance(x, str):
            continue
        u = x.strip().strip("()[]<>.,;")
        if not u:
            continue
        if not re.match(r"^https?://", u, flags=re.IGNORECASE):
            continue
        out.append(u)
    return out

def _normalize_list_str(x: Any) -> List[str]:
    if not x:
        return []
    if isinstance(x, list):
        out = []
        for v in x:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    return []

def _normalize_sections(x: Any) -> List[Dict[str, str]]:
    if not x or not isinstance(x, list):
        return []
    out: List[Dict[str, str]] = []
    for it in x:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        body = str(it.get("body") or "").strip()
        if not title and not body:
            continue
        out.append({"title": title, "body": body})
    return out

def _perplexity_json(
    user_message: str,
    mode: Optional[str],
    profile: Optional[Dict[str, Any]],
    history: Optional[List[Dict[str, Any]]],
) -> Tuple[Optional[Dict[str, Any]], str]:
    if not PPLX_API_KEY:
        return None, "Ошибка: PPLX_API_KEY не найден в .env. Проверь файл .env."

    user_message = (user_message or "").strip()
    if not user_message:
        return None, "Пустой запрос. Напишите вопрос текстом."

    if len(user_message) > USER_MESSAGE_MAX_CHARS:
        user_message = user_message[:USER_MESSAGE_MAX_CHARS].rstrip()

    if mode == "country":
        system_prompt = COUNTRY_INFO_PROMPT
        schema = (
            "{"
            "\"country\":\"<строка>\","
            "\"sections\":[{\"title\":\"<строка>\",\"body\":\"<строка>\"}],"
            "\"sources\":[\"<url>\"]"
            "}"
        )
        user_content = (
            "Верни ОДИН объект JSON строго по схеме ниже и без какого-либо текста вокруг.\n"
            f"Схема: {schema}\n"
            "Требования:\n"
            "- Ответ полностью на русском.\n"
            "- sections: ровно 8 секций, каждая с title и body.\n"
            "- Заголовки секций должны быть по смыслу такими:\n"
            "  1) Основные способы переезда\n"
            "  2) Визы и ВНЖ\n"
            "  3) Работа\n"
            "  4) Учёба\n"
            "  5) Стоимость жизни\n"
            "  6) Кратко о стране\n"
            "  7) Официальные источники\n"
            "  8) Дисклеймер\n"
            "- body: 1–3 коротких предложения, без HTML и без markdown.\n"
            "- sources: только реальные URL. Если не уверен в точном URL, не добавляй его.\n"
            "Запрос пользователя (страна): "
            + user_message
        )
    else:
        system_prompt = MIGRATION_ASSISTANT_SYSTEM_PROMPT
        schema = (
            "{"
            "\"answer\":\"<строка>\","
            "\"clarify\":[\"<строка>\"],"
            "\"sources\":[\"<url>\"]"
            "}"
        )

        ctx = "\n\n".join(
            x for x in [
                (f"Режим: {mode}" if mode else ""),
                _build_profile_context(profile),
                _build_history_context(history),
            ] if x.strip()
        )
        user_content = (
            "Верни ОДИН объект JSON строго по схеме ниже и без какого-либо текста вокруг.\n"
            f"Схема: {schema}\n"
            "Требования:\n"
            "- Ответ на русском.\n"
            "- answer: 2–8 коротких предложений, дружелюбно и по делу, как в чате.\n"
            "- Не используй канцелярит, не делай длинных вступлений.\n"
            "- clarify: 0–2 уточняющих вопроса только если реально не хватает данных.\n"
            "- sources: только реальные URL. Если не уверен в точном URL, не добавляй его.\n"
            "- Не используй markdown.\n"
            "\n"
            + (ctx + "\n\n" if ctx else "")
            + "Сообщение пользователя: "
            + user_message
        )


    payload = {
        "model": PPLX_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
    }

    headers = {
        "Authorization": f"Bearer {PPLX_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = _session.post(PPLX_URL, json=payload, headers=headers, timeout=(10, 60))
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            body = (resp.text or "")[:1500]
            return None, f"Ошибка HTTP {resp.status_code}: {body}"

        try:
            data = resp.json()
        except ValueError:
            body = (resp.text or "")[:1500]
            return None, f"Ошибка: ответ не JSON. HTTP {resp.status_code}: {body}"

        raw = ""
        if isinstance(data, dict) and data.get("choices"):
            raw = (data["choices"][0]["message"]["content"] or "").strip()
        elif isinstance(data, dict) and "output_text" in data:
            raw = (data.get("output_text") or "").strip()
        elif isinstance(data, dict) and "error" in data:
            err = data.get("error") or {}
            return None, f"Ошибка от модели: {err.get('message', 'unknown error')}"
        else:
            return None, f"Неожиданный ответ модели: {data}"

        obj = _safe_json_loads(raw)
        if not obj:
            return None, _cleanup_text(raw)
        return obj, raw

    except requests.Timeout:
        return None, "Ошибка: таймаут при обращении к сервису поиска. Попробуйте ещё раз."
    except requests.exceptions.SSLError:
        return None, "Сейчас не удалось подключиться к сервису поиска. Попробуйте ещё раз через минуту."
    except requests.exceptions.ConnectionError:
        return None, "Сейчас не удалось подключиться к сервису поиска. Попробуйте ещё раз через минуту."
    except Exception as e:
        return None, f"Ошибка при обращении к модели: {e}"

def _openai_domain_gate(user_message: str, mode: Optional[str]) -> Optional[Tuple[bool, str]]:
    if not DOMAIN_GATE_ENABLED:
        return None
    client = _get_openai_client()
    if not client:
        return None
    text = (user_message or "").strip()
    if not text:
        return None
    if mode == "country":
        sys = (
            "Ты маршрутизатор запросов для раздела «справка по стране» в телеграм-боте про миграцию.\n"
            "Определи, является ли сообщение запросом справки по стране (например: 'Германия', 'Нидерланды', 'Расскажи про Канаду').\n"
            "\n"
            "Верни строго один JSON без текста вокруг:\n"
            "{\"in_scope\": true/false, \"reply\": \"<строка>\"}\n"
            "\n"
            "Правила:\n"
            "1) Если пользователь реально просит справку по стране или написал название страны — in_scope=true, reply=\"\".\n"
            "2) Если сообщение не похоже на страну, но это привет/как дела/спасибо/кто ты — in_scope=false и reply: коротко ответь 1–2 предложения и попроси ввести страну (пример).\n"
            "3) Если сообщение не похоже на страну — in_scope=false и reply: вежливо попроси ввести название страны (пример).\n"
            "\n"
            "reply всегда на русском, без HTML и без markdown. Не используй символы < и >.\n"
        )
        default_reply = "Этот раздел — справка по стране. Напишите название страны, например: Германия или Нидерланды."
    else:
        sys = (
            "Ты маршрутизатор запросов для телеграм-бота про международную миграцию.\n"
            "Основная тема бота: визы, ВНЖ/ПМЖ, гражданство, работа/учёба за рубежом, документы для переезда, выбор страны, жизнь и адаптация за границей.\n"
            "\n"
            "Твоя задача: решить, передавать ли запрос основному ИИ.\n"
            "Верни строго один JSON без текста вокруг:\n"
            "{\"in_scope\": true/false, \"reply\": \"<строка>\"}\n"
            "\n"
            "Правила:\n"
            "1) Если запрос по теме миграции — in_scope=true, reply=\"\".\n"
            "2) Если запрос НЕ по теме, но это нормальная бытовая коммуникация (привет, как дела, спасибо, кто ты, что умеешь, как пользоваться ботом) — in_scope=false и reply: короткий дружелюбный ответ 1–2 предложения + в конце мягко предложи помощь по миграции.\n"
            "3) Если запрос НЕ по теме и это что-то нейтральное и простое, на что можно ответить очень коротко и безопасно — можешь дать 1 короткое предложение по сути, затем мягко вернуть к миграции.\n"
            "4) Если запрос НЕ по теме и требует длинной консультации в другой области — in_scope=false и reply: вежливо скажи, что бот про миграцию, и попроси переформулировать в миграционном контексте.\n"
            "5) Если запрос опасный/вредный, медицинский, про самоповреждение, незаконные действия — in_scope=false и reply: вежливый отказ без инструкций + предложи задать вопрос по миграции.\n"
            "6) Если запрос состоит из одного-двух слов типа 'привет', 'ку', 'йо' — ответь очень коротко, без лишнего.\n"
            "\n"
            "reply всегда на русском, без HTML и без markdown. Не используй символы < и >.\n"
        )
        default_reply = (
            "Я специализируюсь на вопросах международной миграции и переезда (визы, ВНЖ, работа/учёба, выбор страны). "
            "Сформулируйте вопрос в миграционном контексте — и я помогу."
        )

    try:
        resp = client.responses.create(
            model=DOMAIN_GATE_MODEL,
            max_output_tokens=160,
            input=[
                {"role": "system", "content": sys},
                {"role": "user", "content": text},
            ],
            store=False,
        )

        out = (resp.output_text or "").strip()
        obj = _safe_json_loads(out)
        if not obj:
            return None

        in_scope = bool(obj.get("in_scope"))
        reply = str(obj.get("reply") or "").strip()

        if in_scope:
            return True, ""

        return False, (reply or default_reply)
    except Exception:
        return None

def _openai_get_text(data: Dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    out_text = (data.get("output_text") or "").strip()
    if out_text:
        return out_text
    output = data.get("output")
    if not isinstance(output, list):
        return ""
    parts: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if isinstance(c, dict) and c.get("type") == "output_text":
                t = (c.get("text") or "").strip()
                if t:
                    parts.append(t)
    return "\n".join(parts).strip()


def _openai_render_from_json(user_message: str, mode: Optional[str], obj: Dict[str, Any]) -> Optional[str]:
    client = _get_openai_client()
    if not client:
        return None
    if mode == "country":
        sys = (
            "Ты редактор справки по стране для телеграм-бота.\n"
            "Тебе дают JSON с секциями и источниками.\n"
            "Собери итоговый ответ по-русски в Telegram HTML.\n"
            "Разрешены только теги: <b>, <i>, <u>, <s>, <code>, <pre>, <a href=\"...\">...</a>.\n"
            "Структура: ровно 8 блоков. Каждый блок начинается с заголовка вида <b>1. ...</b> и далее 1–3 коротких предложения.\n"
            "Не добавляй факты, цифры, сроки, требования и ссылки, которых нет в JSON.\n"
            "Не используй markdown.\n"
            "Между блоками оставляй пустую строку.\n"
            "Если sources есть, используй их только в блоке 7 «Официальные источники».\n"
            "Никогда не используй символы < и > в обычном тексте."
        )
    else:
        sys = (
            "Ты редактор ответов миграционного бота.\n"
            "Собери итоговый ответ по-русски, как живое общение в чате.\n"
            "Формат вывода: Telegram HTML. Разрешены только теги: <b>, <i>, <u>, <s>, <code>, <pre>, <a href=\"...\">...</a>.\n"
            "Не используй markdown.\n"
            "Ответ должен быть коротким и человечным: 1–2 абзаца.\n"
            "Если в JSON есть clarify, добавь в конце блок <b>Уточню:</b> и 1–2 вопроса.\n"
            "Если есть sources, добавь в конце блок <b>Официальные источники:</b> и перечисли URL строками.\n"
            "Запрещено добавлять новые факты, цифры, сроки, требования и URL. Используй только то, что есть в JSON.\n"
            "Никогда не используй символы < и > в обычном тексте."
        )

    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            instructions=sys,
            input=[
                {
                    "role": "user",
                    "content": "Вопрос пользователя:\n"
                    + (user_message or "")
                    + "\n\nJSON:\n"
                    + json.dumps(obj, ensure_ascii=False),
                }
            ],
            store=False,
        )

        out = (resp.output_text or "").strip()
        if not out:
            return None
        return _cleanup_text(out)
    except Exception:
        return None

def _fallback_render(obj: Dict[str, Any], mode: Optional[str]) -> str:
    sources = _normalize_sources(obj.get("sources"))
    sections = _normalize_sections(obj.get("sections"))
    parts: List[str] = []
    if mode == "country":
        for s in sections:
            title = (s.get("title") or "").strip()
            body = (s.get("body") or "").strip()
            if title:
                parts.append(title)
            if body:
                parts.append(body)
        if sources:
            parts.append("Источники:")
            for u in sources[:10]:
                parts.append(u)
        return "\n\n".join([p for p in parts if p.strip()]).strip()
    answer = str(obj.get("answer") or "").strip()
    if answer:
        parts.append(answer)
    clarify = _normalize_list_str(obj.get("clarify"))
    if clarify:
        parts.append("Уточню:")
        for x in clarify[:2]:
            parts.append(f"• {x}")
    if sources:
        parts.append("Официальные источники:")
        for u in sources[:10]:
            parts.append(u)

    return "\n\n".join([p for p in parts if p.strip()]).strip()

def ask_llm(
    user_message: str,
    mode: Optional[str] = None,
    profile: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    gate = _openai_domain_gate(user_message, mode)
    if gate is not None and gate[0] is False:
        return gate[1]

    obj, raw_or_err = _perplexity_json(user_message, mode, profile, history)
    if obj is None:
        return raw_or_err

    cleaned_obj: Dict[str, Any] = dict(obj)
    cleaned_obj["sources"] = _normalize_sources(cleaned_obj.get("sources"))
    cleaned_obj["sections"] = _normalize_sections(cleaned_obj.get("sections"))
    if mode != "country":
        cleaned_obj["clarify"] = _normalize_list_str(cleaned_obj.get("clarify"))
    rendered = _openai_render_from_json(user_message, mode, cleaned_obj)
    if rendered:
        return rendered

    return _fallback_render(cleaned_obj, mode)
