"""Прямые вызовы Bot API для методов Managed Bots (getManagedBotToken и др.),
которых может не быть в установленной версии aiogram. Только стандартная
библиотека."""

import asyncio
import json
import urllib.error
import urllib.request


class TgError(Exception):
    pass


def _call_sync(token: str, method: str, params: dict) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            data = json.load(exc)
        except Exception:
            raise TgError(f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise TgError(str(exc)) from exc
    if not data.get("ok"):
        raise TgError(data.get("description", "unknown error"))
    return data["result"]


async def call(token: str, method: str, **params):
    return await asyncio.to_thread(_call_sync, token, method, params)


def parse_managed_bot(mb: dict) -> tuple[dict, dict]:
    """Из события managed_bot достаёт (пользователь-создатель, созданный бот).
    В реальном событии бот лежит в поле bot_user (по документации и в старых
    описаниях встречалось bot), поддерживаем оба."""
    return (mb.get("user") or {}), (mb.get("bot_user") or mb.get("bot") or {})


async def get_managed_bot_token(manager_token: str, bot_id: int) -> str:
    """Токен управляемого бота. Название параметра у метода пробуем по очереди,
    в ошибке покажем все попытки."""
    errors = []
    for field in ("user_id", "bot_id", "bot_user_id"):
        try:
            result = await call(manager_token, "getManagedBotToken", **{field: bot_id})
            return result if isinstance(result, str) else result["token"]
        except TgError as exc:
            errors.append(f"{field}: {exc}")
    raise TgError("getManagedBotToken не принял параметры: " + "; ".join(errors))


async def get_me(token: str) -> dict:
    return await call(token, "getMe")
