"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.core.state
"""

from .app.core.state import *  # noqa: F401,F403
from .app.core.state import (  # noqa: F401
    MAX_RUNS,
    PID_FILE,
    STATE_DIR,
    STATE_FILE,
    acquire_lock,
    get_state,
    record_run,
    release_lock,
    save_state,
)
