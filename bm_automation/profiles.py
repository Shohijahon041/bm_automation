"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.core.profiles
"""

from .app.core.profiles import *  # noqa: F401,F403
from .app.core.profiles import (  # noqa: F401
    DEFAULT_PROFILES,
    PROFILES_FILE,
    all_profiles,
    discover_from_oneid,
    get_profile,
    load_profiles,
    save_profiles,
    set_active,
    upsert_profile,
)
