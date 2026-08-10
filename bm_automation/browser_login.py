"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.auth.browser_login
"""

from .app.auth.browser_login import *  # noqa: F401,F403
from .app.auth.browser_login import (  # noqa: F401
    ONEID_HREF_PART,
    SITE_BASES,
    browser_login,
    run,
)
