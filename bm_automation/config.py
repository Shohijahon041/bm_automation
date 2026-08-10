"""Backward-compatibility shim.

Haqiqiy kod: bm_automation.app.config.settings
Ushbu fayl eski `from .config import ...` importlarini buzmaslik uchun
qoldirilgan va faqat qayta eksport qiladi.
"""

from .app.config import *  # noqa: F401,F403
from .app.config import __all__  # noqa: F401
