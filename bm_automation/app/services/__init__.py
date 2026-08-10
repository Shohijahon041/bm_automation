"""bm_automation.app.services — business logic qatlami.

Bu yerda: API'dan ma'lumot yig'ish + fayllarga yozish + Telegram'ga
yuborishning ORKESTRATSIYASI. Alohida tarmoqlar:
  - API so'rovlar: app.repositories / app.api.client
  - Excel/PNG yaratish: app.exporters
  - Telegram yuborish: app.notifications
"""

from .report_service import generate_daily_reports, generate_report  # noqa: F401

__all__ = ["generate_daily_reports", "generate_report"]
