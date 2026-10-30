"""TOTP (RFC 6238) — faqat standart kutubxona.

6 xonali kod, 30 soniya qadam, HMAC-SHA1 — Google Authenticator, Authy,
Microsoft Authenticator ilovalari bilan mos (otpauth:// sxemasi).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote

DIGITS = 6
STEP_S = 30
# Soat farqiga chidam: oldingi/keyingi bitta qadam ham qabul qilinadi.
WINDOW_STEPS = 1
ISSUER = "BM Dashboard"


def new_secret(nbytes: int = 20) -> str:
    """Yangi base32 sir (160 bit) — ilovaga QR orqali kiritiladi."""
    return base64.b32encode(os.urandom(nbytes)).decode("ascii").rstrip("=")


def normalize_secret(secret: str) -> str:
    return "".join(str(secret or "").split()).upper().rstrip("=")


def _key(secret: str) -> bytes:
    clean = normalize_secret(secret)
    if not clean:
        raise ValueError("TOTP sir bo'sh")
    pad = "=" * (-len(clean) % 8)
    return base64.b32decode(clean + pad, casefold=True)


def code_at(secret: str, at: float | None = None) -> str:
    """Berilgan vaqt uchun 6 xonali kod (testlar uchun ham ochiq)."""
    counter = int((time.time() if at is None else at) // STEP_S)
    digest = hmac.new(_key(secret), struct.pack(">Q", counter),
                      hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    part = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(part % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, at: float | None = None,
           window_steps: int = WINDOW_STEPS) -> bool:
    """Kodni tekshiradi (±`window_steps` qadam); vaqt bo'yicha doimiy solishtirish."""
    given = "".join(str(code or "").split())
    if len(given) != DIGITS or not given.isdigit():
        return False
    now = time.time() if at is None else at
    ok = False
    try:
        for step in range(-window_steps, window_steps + 1):
            expected = code_at(secret, now + step * STEP_S)
            if hmac.compare_digest(expected, given):
                ok = True
    except ValueError:  # buzilgan/bo'sh sir — kod hech qachon mos kelmaydi
        return False
    return ok


def otpauth_url(secret: str, account: str) -> str:
    """Ilovaga skanerlanadigan `otpauth://` havola."""
    label = quote(f"{ISSUER}:{account or 'user'}", safe="")
    params = f"secret={normalize_secret(secret)}&issuer={quote(ISSUER)}" \
             f"&algorithm=SHA1&digits={DIGITS}&period={STEP_S}"
    return f"otpauth://totp/{label}?{params}"
