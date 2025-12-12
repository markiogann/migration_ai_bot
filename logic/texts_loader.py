import json
from pathlib import Path
from typing import Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent

_SEARCH_DIRS = [
    BASE_DIR / "data",
    BASE_DIR / "logic",
    BASE_DIR,
]

def _find_file(name: str) -> Optional[Path]:
    for d in _SEARCH_DIRS:
        path = d / name
        if path.exists():
            return path
    return None


MESSAGES_FILE = _find_file("messages.json")
POPULAR_COUNTRIES_FILE = _find_file("popular_countries.json")

_messages_cache: Dict[str, str] = {}
_popular_countries_cache: Dict[str, Dict[str, Any]] = {}


def _load_messages() -> None:
    global _messages_cache

    if not MESSAGES_FILE:
        print("[texts_loader] messages.json not found in any known dir")
        _messages_cache = {}
        return

    try:
        with MESSAGES_FILE.open("r", encoding="utf-8") as f:
            _messages_cache = json.load(f)
    except Exception as e:
        print("[texts_loader] error loading messages.json:", repr(e))
        _messages_cache = {}


def _load_popular_countries() -> None:
    global _popular_countries_cache

    if not POPULAR_COUNTRIES_FILE:
        print("[texts_loader] popular_countries.json not found in any known dir")
        _popular_countries_cache = {}
        return

    try:
        with POPULAR_COUNTRIES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            _popular_countries_cache = data
        else:
            print("[texts_loader] popular_countries.json must contain object at top level")
            _popular_countries_cache = {}
    except Exception as e:
        print("[texts_loader] error loading popular_countries.json:", repr(e))
        _popular_countries_cache = {}


def msg(key: str, default: Optional[str] = None) -> Optional[str]:
    if not _messages_cache:
        _load_messages()
    return _messages_cache.get(key, default)


def get_popular_countries() -> Dict[str, Dict[str, Any]]:
    if not _popular_countries_cache:
        _load_popular_countries()
    return _popular_countries_cache


def get_country_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    countries = get_popular_countries()
    return countries.get(slug)
