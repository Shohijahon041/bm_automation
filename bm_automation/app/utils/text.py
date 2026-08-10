"""Matn bilan ishlash yordamchilari.

Turli modullarda takrorlangan Unicode (UZ) harflarni tozalash, sarlavha
ko'rinishiga keltirish va normalizatsiya funksiyalari shu yerda birlashtirilgan.
"""

from __future__ import annotations

import re

__all__ = ["normalize_uz", "upper_norm", "title_case", "collapse_ws"]

# Unicode apostrof / qo'shtirnoqlarni ASCII ekvivalentlarga aylantirish.
# excel_fill._TRANSL va driver_sheet.UZ_FIX bilan bir xil maqsad.
_UZ_TRANSL = str.maketrans({
    "\u02BB": "'",  # ʻ
    "\u02BC": "'",  # ʼ
    "\u2018": "'",  # ‘
    "\u2019": "'",  # ’
    "\u201A": "'",  # ‚
    "\u201B": "'",  # ‛
    "\u201C": '"',  # “
    "\u201D": '"',  # ”
})

_WS_RE = re.compile(r"\s+")


def collapse_ws(s) -> str:
    """Bo'sh joylarni bitta probelga keltirib, chetlarini tozalaydi."""
    return _WS_RE.sub(" ", str(s or "")).strip()


def normalize_uz(s) -> str:
    """UZ maxsus belgilarini ASCII'ga aylantiradi va probellarni yig'adi."""
    return collapse_ws(str(s or "").translate(_UZ_TRANSL))


def upper_norm(s) -> str:
    """normalize_uz + UPPER."""
    return normalize_uz(s).upper()


def title_case(s: str) -> str:
    """Har so'zning birinchi harfini katta qiladi (matn tozalangan holda)."""
    return " ".join(w.capitalize() for w in normalize_uz(s).split())
