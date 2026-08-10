"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.scheduler.schedule
"""

from .app.scheduler.schedule import *  # noqa: F401,F403
from .app.scheduler.schedule import (  # noqa: F401
    main,
    run_daily_job,
    run_scheduler,
)
