import os
import re
import json
from typing import Optional, Dict, Any, List, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

from logic.prompts import MIGRATION_ASSISTANT_SYSTEM_PROMPT
from logic.prompts_country_info import COUNTRY_INFO_PROMPT

load_dotenv()

PPLX_API_KEY = os.getenv("PPLX_API_KEY")
PPLX_URL = "https://api.perplexity.ai/chat/completions"
PPLX_MODEL = os.getenv("PPLX_MODEL", "sonar")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_URL = os.getenv("OPENAI_URL", "https://api.openai.com/v1/responses")

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
    for m in history:
        role = m.get("role")
        text = (m.get("text") or "").strip()
        if not text:
            continue
        prefix = "Пользователь" if role == "user" else "Ассистент"
        lines.append(f"{prefix}: {text}")
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
    if not sources:
        return []
    if isinstance(sources, list):
        out = []
        for x in sources:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out
    return []


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
            "\"topic\":\"<строка>\","
            "\"summary\":\"<строка>\","
            "\"need_to_clarify\":[\"<строка>\"],"
            "\"sections\":[{\"title\":\"<строка>\",\"body\":\"<строка>\"}],"
            "\"next_steps\":[\"<строка>\"],"
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
            "- summary: 2–5 предложений.\n"
            "- need_to_clarify: 0–3 пункта только если реально не хватает данных.\n"
            "- sections: 3–7 секций, структурно.\n"
            "- next_steps: 3–7 конкретных шагов.\n"
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
    if not OPENAI_API_KEY:
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
            "Если sources есть, используй их только в блоке 7 «Источники».\n"
            "Никогда не используй символы < и > в обычном тексте."
        )
    else:
        sys = (
            "Ты редактор ответов миграционного бота.\n"
            "Собери итоговый ответ по-русски, красиво и структурно.\n"
            "Формат вывода: Telegram HTML. Разрешены только теги: <b>, <i>, <u>, <s>, <code>, <pre>, <a href=\"...\">...</a>.\n"
            "Не используй markdown.\n"
            "Запрещено добавлять новые факты, цифры, сроки, требования и URL. Используй только то, что есть в JSON.\n"
            "Делай блоки с пустой строкой между ними.\n"
            "Списки оформляй строками с '• ' в начале.\n"
            "Не делай теги незакрытыми и не растягивай один тег на несколько абзацев.\n"
            "Если есть sources, в конце добавь блок <b>Источники:</b> и перечисли URL строками.\n"
            "Никогда не используй символы < и > в обычном тексте."
        )

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": sys},
            {"role": "user", "content": "Вопрос пользователя:\n" + (user_message or "") + "\n\nJSON:\n" + json.dumps(obj, ensure_ascii=False)},
        ],
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(OPENAI_URL, json=payload, headers=headers, timeout=(10, 60))
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            print(f"[OPENAI] http_error status={resp.status_code} body={(resp.text or '')[:300]}")
            return None

        data = resp.json()
        rid = data.get("id")
        model = data.get("model")
        usage = data.get("usage")
        out = _openai_get_text(data)
        print(f"[OPENAI] ok id={rid} model={model} usage={usage} out_len={len(out)}")

        if not out:
            return None
        return _cleanup_text(out)
    except Exception as e:
        print(f"[OPENAI] exception {e}")
        return None


def _fallback_render(obj: Dict[str, Any], mode: Optional[str]) -> str:
    sources = _normalize_sources(obj.get("sources"))
    sections = _normalize_sections(obj.get("sections"))

    parts: List[str] = []

    if mode != "country":
        summary = str(obj.get("summary") or "").strip()
        if summary:
            parts.append(summary)

        need = _normalize_list_str(obj.get("need_to_clarify"))
        if need:
            parts.append("Нужно уточнить:")
            for x in need[:3]:
                parts.append(f"• {x}")

    for s in sections:
        title = (s.get("title") or "").strip()
        body = (s.get("body") or "").strip()
        if title:
            parts.append(title)
        if body:
            parts.append(body)

    if mode != "country":
        steps = _normalize_list_str(obj.get("next_steps"))
        if steps:
            parts.append("Следующие шаги:")
            for x in steps[:7]:
                parts.append(f"• {x}")

    if sources:
        parts.append("Источники:")
        for u in sources[:10]:
            parts.append(u)

    return "\n".join([p for p in parts if p.strip()]).strip()


def ask_llm(
    user_message: str,
    mode: Optional[str] = None,
    profile: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    obj, raw_or_err = _perplexity_json(user_message, mode, profile, history)
    if obj is None:
        return raw_or_err

    cleaned_obj: Dict[str, Any] = dict(obj)
    cleaned_obj["sources"] = _normalize_sources(cleaned_obj.get("sources"))
    cleaned_obj["sections"] = _normalize_sections(cleaned_obj.get("sections"))
    if mode != "country":
        cleaned_obj["need_to_clarify"] = _normalize_list_str(cleaned_obj.get("need_to_clarify"))
        cleaned_obj["next_steps"] = _normalize_list_str(cleaned_obj.get("next_steps"))

    rendered = _openai_render_from_json(user_message, mode, cleaned_obj)
    if rendered:
        return rendered

    return _fallback_render(cleaned_obj, mode)
