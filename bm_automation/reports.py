"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.reports.registry + app.services.report_service
"""

from .app.reports.registry import REPORTS, VALID_TYPES, has_excel, report_names  # noqa: F401
from .app.services.report_service import (  # noqa: F401
    _resolve_params,
    export_week_range,
    generate_daily_reports,
    generate_report,
)
