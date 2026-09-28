"""Bot orqali o'zgartiriladigan sozlamalar (`state/bot_settings.json`).

- 1-km narxi yo'nalish darajasida saqlanadi (`route_km`/`set_route_km`),
  global emas — har bir yo'nalish o'z narxiga ega.
- `langs` — chat_id → til (`uz` / `ru`).

Fayl atomik yoziladi, bot qayta ishga tushsa ham saqlanib qoladi.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

from ..utils.io import atomic_write

STATE_FILE = Path("state") / "bot_settings.json"

_LOCK = threading.Lock()
_CACHE: dict = {}
_LOADED = False

# Interaktiv oqimlar uchun kutilayotgan kirish (chat_id -> sozlama kaliti)
_PENDING: dict[int, str] = {}

# Audit tarixining maksimal uzunligi (JSON ichida saqlanadi).
MAX_AUDIT = 200
_AUDIT_KEY = "audit"

# Moliyaviy qiymatlar uchun maksimal chegaralar (so'm/km, so'm/kVt...).
MAX_AMOUNT = 100_000_000_000_000.0


def _clean_amount(value, default: float = 0.0) -> float:
    """Qiymatni xavfsiz musbat songa aylantiradi (nan/inf → `default`).

    NaN, Infinity yoki salbiy qiymatlar JSON'ga asl holida yozilsa
    korrupsiya/hisob-kitob buzilishiga olib kelishi mumkin — barcha
    setterlar bu funksiyadan o'tadi.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return max(float(default), 0.0)
    if not math.isfinite(v):
        return max(float(default), 0.0)
    return min(max(v, 0.0), MAX_AMOUNT)


def _load() -> dict:
    global _LOADED
    if _LOADED:
        return _CACHE
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _CACHE.update(data)
    except Exception:
        pass
    _LOADED = True
    return _CACHE


def _save() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(_CACHE, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - kichik bezak, xato bo'lsa o'tkazib yuboramiz
        print(f"Bot sozlamalari saqlanmadi: {exc}")


def _reset() -> None:
    """Testlar uchun: xotira holatini tozalaydi."""
    global _LOADED
    with _LOCK:
        _CACHE.clear()
        _LOADED = False
        _PENDING.clear()


# --------------------------------------------------------------- audit

def _audit(key: str, old, new, by: str = "") -> None:
    """Sozlama o'zgarishini tarixga yozadi (oxirgi `MAX_AUDIT` ta saglanadi).

    Eslatma: faqat `_LOCK` allaqachon qo'lga olinganida chaqiriladi
    (barcha `set_*` funksiyalari lock ichida ishlaydi).
    """
    history = _CACHE.setdefault(_AUDIT_KEY, [])
    if history and str(history[-1].get("key")) == str(key) and \
            history[-1].get("new") == new and str(history[-1].get("by")) == str(by or ""):
        history[-1]["ts"] = time.time()
        return
    history.append({
        "ts": time.time(),
        "key": str(key),
        "old": old,
        "new": new,
        "by": str(by or ""),
    })
    del history[:-MAX_AUDIT]


def audit_log(limit: int = 20) -> list[dict]:
    """Oxirgi o'zgarishlar tarixi (yangi → eski)."""
    log = []
    try:
        log = list(_load().get(_AUDIT_KEY) or [])
    except Exception:  # noqa: BLE001
        pass
    from datetime import datetime as _dt
    log = sorted(log, key=lambda r: float(r.get("ts") or 0), reverse=True)[:limit]
    out = []
    for r in log:
        try:
            when = _dt.fromtimestamp(float(r.get("ts") or 0)).strftime("%d.%m %H:%M")
        except (ValueError, OSError, TypeError):
            when = "?"
        out.append({
            "when": when,
            "key": str(r.get("key") or "?"),
            "old": r.get("old"),
            "new": r.get("new"),
            "by": str(r.get("by") or ""),
        })
    return out


# ------------------------------------------------------------- km narxi

# 1-km narxi endi global emas — har yo'nalish uchun route_kм darajasida
# saqlanadi (qarang: ``route_km``/``set_route_km``; yangi haydovchiga
# sinxronizatsiyada yo'nalish narxi yoziladi).

# ----------------------------------------------------- elektr energiya narxi

# Elektr energiya hisob-kitobi koeffitsiyenti: 1 km uchun kVt/soat iste'moli
ELEC_KWH_PER_KM = 0.955


def elec_price() -> float:
    """Bot orqali o'rnatilgan global 1 kVt/soat elektr narxi (so'm)."""
    try:
        return float(_load().get("elec_price") or 0)
    except (TypeError, ValueError):
        return 0.0


def set_elec_price(value: float | None) -> float:
    """1 kVt/soat elektr narxini o'rnatadi (0/None — o'chirish)."""
    with _LOCK:
        _load()
        old = elec_price()
        value = _clean_amount(value)
        _CACHE["elec_price"] = value
        _audit("elec_price", old, value)
        _save()
    return value


# ----------------------------------------------------- brutto (116-son)

# Jamoat transporti shartnomasi bo'yicha 1 mashina-km narxi (SKM, so'm).
DEFAULT_SKM = 16176.0


def brutto_skm() -> float:
    """116-son qaror (32-band) uchun 1 mashina-km narxi (SKM)."""
    try:
        v = _load().get("brutto_skm")
        return float(v) if v not in (None, "") else DEFAULT_SKM
    except (TypeError, ValueError):
        return DEFAULT_SKM


def set_brutto_skm(value: float | None) -> float:
    """SKM ni o'rnatadi (0/None — DEFAULT_SKM ga qaytish)."""
    with _LOCK:
        _load()
        old = brutto_skm()
        value = _clean_amount(value, DEFAULT_SKM)
        _CACHE["brutto_skm"] = value or DEFAULT_SKM
        _audit("brutto_skm", old, _CACHE["brutto_skm"])
        _save()
    return brutto_skm()


def route_skm(route_id: str, default: float = 0.0) -> float:
    """Yo'nalish (firma) uchun 1 mashina-km SKM (so'm/km).

    O'rnatilgan yo'nalish SKM si bo'lsa shu qaytariladi; bo'lmasa `default`
    (global `brutto_skm()` chaqiruvchidan uzatiladi). 0/saqlanmagan — 0.
    """
    try:
        r = _load().get("route_skm") or {}
        v = r.get(str(route_id or ""))
        if v not in (None, ""):
            return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return max(float(default), 0.0)
    except (TypeError, ValueError):
        return 0.0


def set_route_skm(route_id: str, value: float | None) -> float:
    """Yo'nalish (firma) uchun 1 mashina-km SKM ni o'rnatadi.

    value 0/None — yo'nalish qaydini o'chiradi (global SKM ga qaytadi).
    """
    with _LOCK:
        _load()
        skms = _CACHE.setdefault("route_skm", {})
        old = skms.get(str(route_id or ""), 0.0)
        v = _clean_amount(value)
        if v > 0:
            skms[str(route_id or "")] = v
        else:
            skms.pop(str(route_id or ""), None)
        _audit("route_skm:" + str(route_id or ""), old, v)
        _save()
    return route_skm(str(route_id or ""))


# ------------------------------------------------- yo'nalish km narxi

def route_km(route_id: str, default: float = 0.0) -> float:
    """Yo'nalish uchun 1 km narxi (so'm).

    Dashboard'dan yo'nalish darajasida o'rnatilgan qiymat (haydovchidan
    qat'i nazar). 0/saqlanmagan — 0 (chaqiruvchi standart ustunlikdan
    foydalanadi).
    """
    try:
        r = _load().get("route_km") or {}
        v = r.get(str(route_id or ""))
        if v not in (None, ""):
            return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return max(float(default), 0.0)
    except (TypeError, ValueError):
        return 0.0


def set_route_km(route_id: str, value: float | None) -> float:
    """Yo'nalish uchun 1 km narxini o'rnatadi.

    value 0/None — yo'nalish qaydini o'chiradi (standart ustunlikka qaytadi).
    """
    with _LOCK:
        _load()
        kms = _CACHE.setdefault("route_km", {})
        old = kms.get(str(route_id or ""), 0.0)
        v = _clean_amount(value)
        if v > 0:
            kms[str(route_id or "")] = v
        else:
            kms.pop(str(route_id or ""), None)
        _audit("route_km:" + str(route_id or ""), old, v)
        _save()
    return route_km(str(route_id or ""))


# ------------------------------------------------------- yo'nalish tarifi

# 10-yo'nalish uchun boshlang'ich narx (dastlabki qiymat, qancha so'm/km):
#   no_vat — QQSsiz, vat — QQS bilan (boshlang'ich narx miqdori).
DEFAULT_ROUTE_TARIFFS: dict[str, dict] = {
    "dfbfbe00-38a2-4ecc-8f3b-15b790308cbc": {"no_vat": 12185.0, "vat": 13647.0},
}


def route_tariff(route_id: str) -> dict:
    """Yo'nalish uchun 1 km narxi (so'm): {'no_vat': ..., 'vat': ...}.

    Saqlangan qiymat bo'lmasa, `DEFAULT_ROUTE_TARIFFS` dan olinadi; u ham
    bo'lmasa nollar qaytadi (narx o'rnatilmagan).
    """
    try:
        r = _load().get("route_tariff") or {}
    except Exception:
        r = {}
    item = r.get(str(route_id or "")) or {}
    dflt = DEFAULT_ROUTE_TARIFFS.get(str(route_id or ""), {})
    try:
        no_vat = float(item.get("no_vat") if item.get("no_vat") not in (None, "")
                       else dflt.get("no_vat") or 0)
    except (TypeError, ValueError):
        no_vat = 0.0
    try:
        vat = float(item.get("vat") if item.get("vat") not in (None, "")
                    else dflt.get("vat") or 0)
    except (TypeError, ValueError):
        vat = 0.0
    return {"no_vat": no_vat, "vat": vat}


def set_route_tariff(route_id: str, no_vat: float | None,
                     vat: float | None) -> dict:
    """Yo'nalish uchun 1 km narxini o'rnatadi (so'm/km)."""
    with _LOCK:
        _load()
        tariffs = _CACHE.setdefault("route_tariff", {})
        cur = dict(tariffs.get(str(route_id or ""), {}))
        old = dict(cur)
        cur["no_vat"] = _clean_amount(no_vat)
        cur["vat"] = _clean_amount(vat)
        tariffs[str(route_id or "")] = cur
        _audit("route_tariff:" + str(route_id or ""), old, dict(cur))
        _save()
    return dict(cur)


# ------------------------------------------------------------------- til

def lang(chat_id: int) -> str:
    """Foydalanuvchi tili (`uz` / `ru`)."""
    langs = _load().get("langs") or {}
    return str(langs.get(str(chat_id), "uz")) if str(langs.get(str(chat_id), "uz")) in ("uz", "ru") else "uz"


def set_lang(chat_id: int, value: str) -> str:
    """Foydalanuvchi tilini o'rnatadi (`uz` / `ru`)."""
    value = value if value in ("uz", "ru") else "uz"
    with _LOCK:
        _load()
        _CACHE.setdefault("langs", {})[str(chat_id)] = value
        _save()
    return value


# --------------------------------------------------- interaktiv sozlamalar

def pending(chat_id: int) -> str | None:
    """chat_id uchun kutilayotgan sozlama oqimi kaliti (masalan `km_rate`)."""
    return _PENDING.get(chat_id)


def set_pending(chat_id: int, key: str) -> None:
    _PENDING[chat_id] = key


def clear_pending(chat_id: int) -> None:
    _PENDING.pop(chat_id, None)
