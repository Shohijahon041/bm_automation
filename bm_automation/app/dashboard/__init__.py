"""Web dashboard — BM Automation metrikalarini ko'rsatadi.

- `metrics` — DB asosidagi KPI/yo'nalish/avtobus/haydovchi/tizim metrikalari;
- `export` — CSV/Excel/PDF eksport;
- `fastapi_app` — FastAPI ilovasi (router'lar `routers/` paketida).

Ishga tushirish: `python -m bm_automation dashboard`.
"""

from . import export, metrics  # noqa: F401

__all__ = ["export", "metrics"]
