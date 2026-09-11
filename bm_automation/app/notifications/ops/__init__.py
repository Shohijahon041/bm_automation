"""Transport Operations Bot — professional Telegram bot.

Buyruqlar: /start /status /today /routes /vehicles /drivers /trips /problems
/reports /export /sync /errors /insights /help + inline navigatsiya.

Role-based access: ADMIN / DISPATCHER / VIEWER (ops.roles).
"""

from .ops import main, poll_forever
from .text import HELP_TEXT

__all__ = ["main", "poll_forever", "HELP_TEXT"]
