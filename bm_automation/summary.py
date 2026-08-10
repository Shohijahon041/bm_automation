"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.services.summary_service
"""

from .app.services.summary_service import *  # noqa: F401,F403
from .app.services.summary_service import (  # noqa: F401
    DUTY_FILE_RE,
    collect_days,
    driver_summary,
    monthly_rows,
    run,
)

# Eski nom: exporters.excel_export.monthly_excel
from .app.exporters.excel_export import monthly_excel  # noqa: F401
