"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.services.excel_fill_service
"""

from .app.services.excel_fill_service import *  # noqa: F401,F403
from .app.services.excel_fill_service import (  # noqa: F401
    DUTY,
    DRIVERS,
    SHIFTS,
    VEHICLES,
    build_payload,
    fill_duty,
    make_plan_report,
    parse_plan_excel,
    resolve_driver_id,
    resolve_ids,
    resolve_vehicle_id,
    run,
)
