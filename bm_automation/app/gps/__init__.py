"""GPS moduli — Teltonika FMB920 qabul servisi + video kuzatuv registri.

Mavjud loyihaga NOL ta'sir:
- bu paket faqat O'Z jadvallarini yaratadi (CREATE TABLE IF NOT EXISTS)
- mavjud jadvallardan FAQAT O'QIYDI (vehicles.plate_number)
- dashboard/server.py, schema.py, CLI o'zgarmaydi

Ishga tushirish:
    python -m bm_automation.app.gps
"""

from . import codec  # noqa: F401
