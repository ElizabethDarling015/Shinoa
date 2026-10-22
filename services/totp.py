"""
Локальный генератор кодов 2FA (TOTP, RFC 6238) — без сторонних сайтов.
Ничего не хранит: считает код по ключу, который прислали.
"""

import base64
import hashlib
import hmac
import struct
import time
from urllib.parse import urlparse, parse_qs, unquote


ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


# ──────────────────────────────────────────────
# Генерация кодов
# ──────────────────────────────────────────────

def normalize_secret(raw: str) -> str:
    """Приводит ключ к виду base32: без пробелов и дефисов, заглавными, с паддингом."""
    s = "".join(raw.split()).replace("-", "").upper().rstrip("=")
    return s + "=" * (-len(s) % 8)


def secret_is_valid(secret: str) -> bool:
    try:
        return len(base64.b32decode(normalize_secret(secret), casefold=True)) >= 5
    except Exception:
        return False


def generate_code(secret: str, digits: int = 6, period: int = 30,
                  algorithm: str = "SHA1", for_time: float | None = None) -> str:
    """Текущий TOTP-код для ключа."""
    key = base64.b32decode(normalize_secret(secret), casefold=True)
    counter = int((time.time() if for_time is None else for_time) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), ALGORITHMS.get(algorithm.upper(), hashlib.sha1)).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def seconds_left(period: int = 30, now: float | None = None) -> int:
    """Сколько секунд ещё действует текущий код."""
    now = time.time() if now is None else now
    return period - int(now) % period


# ──────────────────────────────────────────────
# Разбор того, что прислал пользователь
# ──────────────────────────────────────────────

def parse_user_input(text: str) -> dict | None:
    """
    Принимает «голый» ключ (JBSW Y3DP …) или ссылку otpauth://totp/…
    Возвращает {secret, digits, period, algorithm, label} или None.
    """
    text = text.strip()
    if text.lower().startswith("otpauth://"):
        u = urlparse(text)
        if u.netloc.lower() != "totp":
            return None  # HOTP (счётчик) не поддерживаем
        q = {k.lower(): v[0] for k, v in parse_qs(u.query).items()}
        secret = q.get("secret", "")
        if not secret_is_valid(secret):
            return None
        try:
            digits = int(q.get("digits", 6))
            period = int(q.get("period", 30))
        except ValueError:
            return None
        algorithm = q.get("algorithm", "SHA1").upper()
        if digits not in (6, 7, 8) or not 10 <= period <= 120 or algorithm not in ALGORITHMS:
            return None
        label = unquote(u.path.lstrip("/")) or q.get("issuer", "")
        return {"secret": normalize_secret(secret), "digits": digits, "period": period,
                "algorithm": algorithm, "label": label}

    if secret_is_valid(text):
        return {"secret": normalize_secret(text), "digits": 6, "period": 30,
                "algorithm": "SHA1", "label": ""}
    return None
