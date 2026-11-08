"""Шифрование токенов зеркал. В базе и бэкапах токены лежат только в
зашифрованном виде, ключ (ENCRYPTION_KEY) хранится отдельно в .env."""

from cryptography.fernet import Fernet

import config

_fernet: Fernet | None = None


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(config.ENCRYPTION_KEY.encode())
    return _fernet


def encrypt(token: str) -> str:
    return _f().encrypt(token.encode()).decode()


def decrypt(value: str) -> str:
    return _f().decrypt(value.encode()).decode()
