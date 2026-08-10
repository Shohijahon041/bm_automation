"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.services.waybill_service
"""

from .app.services.waybill_service import *  # noqa: F401,F403
from .app.services.waybill_service import (  # noqa: F401
    DIRECTIONS,
    MAX_RANGE_DAYS,
    STATUSES,
    WAYBILL,
    run,
    save_json,
    waybill_download,
    waybill_report,
    waybill_stat_plan,
)
