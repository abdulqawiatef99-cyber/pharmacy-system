"""Password hashing and session tokens (stdlib only).

Passwords: scrypt (memory-hard, in the standard library) with a per-password
random salt; parameters are stored in the hash string so they can be raised
later and old hashes still verify (and get re-hashed on next login).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

# N=2**15, r=8 -> ~32 MiB per hash; tests lower N via set_cost().
_COST = {"n": 2 ** 15, "r": 8, "p": 1}
_MAXMEM = 128 * 1024 * 1024

MIN_PASSWORD_LENGTH = 10


def set_cost(n: int) -> None:
    _COST["n"] = n


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    n, r, p = _COST["n"], _COST["r"], _COST["p"]
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                        dklen=32, maxmem=_MAXMEM)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r),
                            p=int(p), dklen=len(expected), maxmem=_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def needs_rehash(stored: str) -> bool:
    try:
        _, n, r, p, *_ = stored.split("$")
        return (int(n), int(r), int(p)) != (_COST["n"], _COST["r"], _COST["p"])
    except ValueError:
        return True


def check_password_policy(password: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"كلمة المرور لازم تكون {MIN_PASSWORD_LENGTH} حروف على الأقل")
    if password.strip() != password:
        problems.append("كلمة المرور لا تبدأ ولا تنتهي بمسافة")
    if len(set(password)) < 4:
        problems.append("كلمة المرور ضعيفة جدًا (حروف متكررة)")
    return problems


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# A real hash used to equalize timing when the username does not exist.
_DUMMY_HASH: str | None = None


def dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None or needs_rehash(_DUMMY_HASH):
        _DUMMY_HASH = hash_password("dummy-password-for-timing")
    verify_password(password, _DUMMY_HASH)
