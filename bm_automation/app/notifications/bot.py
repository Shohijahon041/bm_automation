"""Transport Operations Bot (backward-compatible entrypoint).

Professional bot qatlami `app.notifications.ops` da. Bu modul eski importlar
(`from bm_automation.app.notifications.bot import main, poll_forever, HELP_TEXT`)
buzilmasligi uchun qayta eksport qiladi.
"""

from .ops import main, poll_forever
from .ops.text import HELP_TEXT

__all__ = ["main", "poll_forever", "HELP_TEXT"]
