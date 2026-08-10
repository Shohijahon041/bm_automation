"""Web dashboard — BM Automation metrikalarini ko'rsatadi.

- `metrics` — DB asosidagi KPI/yo'nalish/avtobus/haydovchi/tizim metrikalari;
- `export` — CSV/Excel/PDF eksport;
- `server` — stdlib HTTP server + JSON API (auto-refresh brauzerda).

Ishga tushirish: `python -m bm_automation dashboard`.
"""

from . import export, metrics  # noqa: F401

__all__ = ["export", "metrics"]
