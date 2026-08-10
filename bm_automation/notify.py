"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.notifications.telegram
"""

from .app.notifications.telegram import *  # noqa: F401,F403
from .app.notifications.telegram import (  # noqa: F401
    API,
    command_keyboard,
    export_keyboard,
    resend_keyboard,
    send_document,
    send_message,
    send_photo,
    send_report_summary,
    telegram_call,
)
