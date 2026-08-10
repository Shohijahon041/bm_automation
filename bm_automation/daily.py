"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.services.daily_service
"""

from .app.services.daily_service import *  # noqa: F401,F403
from .app.services.daily_service import (  # noqa: F401
    main,
    run_daily,
    run_daily_once,
)
