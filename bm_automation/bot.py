"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.notifications.bot
"""

from .app.notifications.bot import *  # noqa: F401,F403
from .app.notifications.bot import (  # noqa: F401
    HELP_TEXT,
    main,
    poll_forever,
)
