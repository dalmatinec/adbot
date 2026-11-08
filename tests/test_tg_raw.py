"""Разбор события managed_bot на реальном примере из лога: python3 tests/test_tg_raw.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tg_raw  # noqa: E402

# событие, которое Telegram прислал на самом деле
real = {
    "user": {"id": 7982614573, "is_bot": False, "first_name": "🕊", "last_name": None,
             "username": None, "language_code": "ru"},
    "bot_user": {"id": 9229351894, "is_bot": True, "first_name": "Flux 578",
                 "username": "ads_515d80e0_bot"},
}
user, bot = tg_raw.parse_managed_bot(real)
assert user["id"] == 7982614573 and bot["id"] == 9229351894
assert bot["username"] == "ads_515d80e0_bot"

# старый вариант названия поля тоже работает, пустое событие не падает
assert tg_raw.parse_managed_bot({"user": {"id": 1}, "bot": {"id": 2}})[1]["id"] == 2
assert tg_raw.parse_managed_bot({}) == ({}, {})
print("tg_raw: все проверки пройдены")
