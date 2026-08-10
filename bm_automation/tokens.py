"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.core.tokens
"""

from .app.core.tokens import *  # noqa: F401,F403
from .app.core.tokens import (  # noqa: F401
    TOKENS_FILE,
    TOKEN_TTL_HOURS,
    clear_tokens,
    has_valid_access_token,
    load_tokens,
    save_tokens,
)
