"""Brutto-shartnoma 3-modul: operatsion dispetcherlik va monitoring.

L_f (amalda masofa) va K_amal (amalda qatnovlar) GPS orqali real-vaqtda
kuzatiladi. Quyidagi hollar bajarilmagan qatnov DEB HISOBLANMAYDI:
tirbandlik, YTH (yo'l transport hodisasi), GPS texnik nosozligi.
"""

from __future__ import annotations

# Bajarilmagan qatnov sanalmaydigan sabablar (dispetcherlik istisnolari)
EXCLUDED_EXCUSES: tuple[str, ...] = (
    "tirbandlik",
    "yth",
    "yo'l transport hodisasi",
    "gps nosozlik",
    "gps texnik nosozlik",
)


def is_excused(reason: str) -> bool:
    """Sabab asosli (bajarilmagan qatnov hisoblanmaydi) ekanligini tekshiradi."""
    r = str(reason or "").strip().lower()
    if not r:
        return False
    return any(ex in r for ex in EXCLUDED_EXCUSES)


def effective_kstjb(kstjb: int, excused_count: int) -> int:
    """Asosli sabablar chiqarib tashlangan Kstjb (sifat indeksi uchun)."""
    return max(int(kstjb or 0) - int(excused_count or 0), 0)


def effective_km(plan_km: float, actual_km: float, reason: str) -> float:
    """L_f hisobi: asosli sabab bo'lsa reja masofa to'liq hisoblanadi.

    Tirbandlik/YTH/GPS nosozlikda haydovchi aybsiz — amalda bajarilgan
    masofaga reja davining to'liq oralig'i qo'llaniladi (L_f = max(Lf, Lr)).
    """
    if is_excused(reason):
        return max(float(plan_km or 0), float(actual_km or 0))
    return float(actual_km or 0)