"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.services.duty_service
"""

from .app.services.duty_service import *  # noqa: F401,F403
from .app.services.duty_service import (  # noqa: F401
    DUTY,
    DUTY_HEADERS,
    duty_by_date,
    graphs_to_rows,
    run,
    to_excel,
)

# Eski nom (HEADERS) — duty.to_excel'da ishlatilgan
HEADERS = DUTY_HEADERS  # noqa: F401
