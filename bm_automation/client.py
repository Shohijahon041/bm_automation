"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.api.client
"""

from .app.api import *  # noqa: F401,F403
from .app.api.client import (  # noqa: F401
    API_ERROR_MSG,
    BMClient,
    BMApiError,
    BMAuthError,
    BMJSONError,
    BMRateLimitError,
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    NETWORK_RETRIES,
    NETWORK_RETRY_DELAY,
    RETRY_BACKOFF_MULTIPLIER,
    RETRY_BASE_DELAY,
    RETRY_MAX_DELAY,
)
