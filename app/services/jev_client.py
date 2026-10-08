"""Клиент decision-модели Jev (typesafe/jev-1.13) на OpenRouter.

Не чат: на вход плоский словарь `state`, на выход типизированный ответ с
вероятностью. Ключ берётся из окружения (OPENROUTER_API_KEY). 529 это
system_overloaded у TypeSafe, приходит регулярно, поэтому ретраи обязательны:
без них в разборе остаются дыры, которые легко не заметить.
"""

import json
import os
import time
import urllib.error
import urllib.request

MODEL = "typesafe/jev-1.13"
URL = "https://openrouter.ai/api/alpha/decisions"
RETRY_CODES = (429, 500, 502, 503, 504, 529)


class JevError(RuntimeError):
    pass


def api_key() -> str:
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise JevError("нет OPENROUTER_API_KEY в окружении")
    return key


def decide(state: dict, questions: dict, timeout: int = 120, retries: int = 3) -> dict:
    """Один запрос со всеми вопросами: state уходит один раз на все вопросы."""
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    headers = {
        "Authorization": "Bearer " + api_key(),
        "Content-Type": "application/json",
    }
    last = None
    for attempt in range(retries):
        request = urllib.request.Request(URL, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:  # noqa: PERF203
            last = f"HTTP {error.code}"
            if error.code in RETRY_CODES:
                time.sleep(2 * (attempt + 1))
                continue
            raise JevError(last) from error
        except Exception as error:  # noqa: BLE001
            last = repr(error)
            time.sleep(2 * (attempt + 1))
    raise JevError(str(last))
