"""O'zbek/Ruscha to'liq ismni qisqartirish (otasining ismisiz).

"RAXIMOV SIROJIDDIN BILOLDINOVICH" -> "RAXIMOV SIROJIDDIN".
2 ta so'zdan iborat ism o'zgarishsiz qoladi.
"""

from __future__ import annotations


def short_name(full: str) -> str:
    """Familiya + ism (otasining ismi tushiriladi)."""
    value = str(full or "")
    tokens = [t for t in value.split() if t]
    if len(tokens) > 2:
        return f"{tokens[0]} {tokens[1]}"
    return value