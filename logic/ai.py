import os
import re
from typing import Optional, Dict, Any, List

import requests
from dotenv import load_dotenv

from logic.prompts import MIGRATION_ASSISTANT_SYSTEM_PROMPT
from logic.prompts_country_info import COUNTRY_INFO_PROMPT

load_dotenv()

PPLX_API_KEY = os.getenv("PPLX_API_KEY")
PPLX_URL = "https://api.perplexity.ai/chat/completions"


def _cleanup_citations_and_markdown(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    cleaned = re.sub(r"\s*\[\d+\]", "", cleaned)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*(.+?)\*", r"\1", cleaned)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
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
    return "Из профиля пользователя известно:\n" + "\n".join(parts) + "\n\n"


def _build_history_context(history: Optional[List[Dict[str, Any]]]) -> str:
    if not history:
        return ""
    lines: List[str] = []
    for m in history:
        role = m.get("role")
        text = m.get("text", "")
        if not text:
            continue
        prefix = "Пользователь" if role == "user" else "Ассистент"
        lines.append(f"{prefix}: {text}")
    if not lines:
        return ""
    return "Краткая история последних сообщений (от старых к новым):\n" + "\n".join(lines) + "\n\n"


def ask_llm(
    user_message: str,
    mode: Optional[str] = None,
    profile: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    if not PPLX_API_KEY:
        return "Ошибка: PPLX_API_KEY не найден в .env. Проверь файл .env."

    if mode == "country":
        system_prompt = COUNTRY_INFO_PROMPT
        user_content = (
            "The user requested a short general migration-related overview for a single country.\n"
            f"Country name (as provided by the user): {user_message}\n"
            "Use ONLY the system prompt for the country-info mode.\n"
            "Generate a structured answer STRICTLY following the required template.\n"
            "Do NOT ask any questions. Do NOT invite the user to continue the conversation.\n"
            "Do NOT add any extra text before section 1 or after the last section of the template.\n"
            "The final answer MUST be fully in Russian."
        )
    else:
        system_prompt = MIGRATION_ASSISTANT_SYSTEM_PROMPT
        profile_context = _build_profile_context(profile)
        history_context = _build_history_context(history)
        extra_mode = f"Выбранный пользователем режим (для контекста): {mode}\n" if mode else ""
        user_content = (
            f"{extra_mode}"
            f"{profile_context}"
            f"{history_context}"
            f"Новое сообщение пользователя (на русском): {user_message}\n"
            "Ответь по-русски, кратко и по делу.\n"
            "Если вопрос слишком общий, сначала коротко ответь, а затем задай 1–3 уточняющих вопроса.\n"
            "Не используй markdown и не используй ссылки вида [1], [2] и т.п.\n"
            "Если вопрос не относится к миграции, визам, ВНЖ, гражданству, работе или учёбе за рубежом, "
            "стоимости жизни и адаптации — вежливо откажись отвечать по сути и попроси переформулировать "
            "вопрос в миграционном контексте.\n"
            "Если вопрос касается виз, ВНЖ, гражданства или официальных правил и ты приводишь источники, "
            "используй только реальные официальные сайты (посольство, государственные порталы) и пиши URL "
            "простым текстом, без скобок и форматирования.\n"
        )

    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
    }

    headers = {
        "Authorization": f"Bearer {PPLX_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(PPLX_URL, json=payload, headers=headers, timeout=(10, 60))

        try:
            resp.raise_for_status()
        except requests.HTTPError:
            body = (resp.text or "")[:1500]
            return f"Ошибка HTTP {resp.status_code}: {body}"

        try:
            data = resp.json()
        except ValueError:
            body = (resp.text or "")[:1500]
            return f"Ошибка: ответ не JSON. HTTP {resp.status_code}: {body}"

        if "choices" in data and data["choices"]:
            raw = data["choices"][0]["message"]["content"]
            return _cleanup_citations_and_markdown(raw or "")
        if "output_text" in data:
            return _cleanup_citations_and_markdown(data["output_text"] or "")
        if "error" in data:
            err = data["error"] or {}
            return f"Ошибка от модели: {err.get('message', 'unknown error')}"
        return f"Неожиданный ответ модели: {data}"

    except requests.Timeout:
        return "Ошибка: таймаут при обращении к модели. Попробуйте ещё раз."
    except Exception as e:
        return f"Ошибка при обращении к модели: {e}"
