"""Kamera oqimlari diagnostikasi uchun umumiy yordamchilar.

Dashboard har bir oqim uchun "oxirgi xato"ni ko'rsatadi — u ffmpeg'ning
stderr logidan olinadi (`-loglevel warning/error`, shuning uchun log kichik).
Faqat oxirgi bir necha KB o'qiladi: API har 5 sekundda chaqirilishi mumkin.
"""

from __future__ import annotations

from pathlib import Path

# ffmpeg har oqimda takrorlaydigan, ammo oqim ishiga TA'SIR QILMAYDIGAN
# ogohlantirishlar. Ular "oxirgi xato" sifatida ko'rsatilsa operator chalg'iydi
# (ayniqsa B-frame'li kameralarda "non monotonically increasing dts").
BENIGN = (
    "deprecated",
    "last message repeated",
    "thread message queue blocking",
    "queue input is backward in time",
    "non monotonically increasing dts",
    "timestamps are unset",
    "application provided invalid",
    "co located pocs unavailable",
)

TAIL_BYTES = 4096
MAX_LINE = 400


def is_benign(line: str) -> bool:
    """Qator ahamiyatsiz (doimiy) ogohlantirishmi?"""
    low = line.lower()
    return any(b in low for b in BENIGN)


def tail_note(path, limit: int = TAIL_BYTES) -> str | None:
    """Log faylining oxiridan oxirgi MA'NOLI qatorni qaytaradi.

    Fayl yo'q / bo'sh / faqat ahamiyatsiz ogohlantirishlardan iborat bo'lsa
    `None`. Fayl mavjud bo'lmasa ham xato otmaydi (diagnostika hech qachon
    asosiy oqimni to'xtatmasligi kerak).
    """
    if not path:
        return None
    try:
        p = Path(path)
        size = p.stat().st_size
        if size <= 0:
            return None
        with p.open("rb") as f:
            if size > limit:
                f.seek(size - limit)
            raw = f.read(limit)
    except OSError:
        return None
    for line in reversed(raw.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if line and not is_benign(line):
            return line[:MAX_LINE]
    return None
