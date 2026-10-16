"""Bo'limlar (sections) — panel va Telegram bot uchun yagona kalitlar.

Panel (dashboard) va bot bir xil bo'lim nomlaridan foydalanadi: admin
foydalanuvchiga qaysi bo'limlar ishlashini belgilasa, bu ikkala tomonda
ham amal qiladi (panel menyusi va bot buyruqlari).

Bu modul faqat ma'lumot/tekshiruv bilan shug'ullanadi (DB/Telegram yo'q):
- `SECTION_KEYS` — ruxsat beriladigan bo'lim kalitlari (panel bilan bir xil);
- `SECTION_LABELS` — o'zbekcha nomlari (xabarlarda ishlatiladi);
- `BOT_SECTIONS` — bot buyruq/nav nomi → bo'lim kaliti;
- `clean_sections`, `sections_allowed` — tekshiruv yordamchilari.
"""

from __future__ import annotations

# Panel `web/index.html` dagi PAGE_ROLES bilan BIR XIL bo'lishi shart:
# yangi bo'lim qo'shilsa — ikkala tomonda ham qo'shiladi.
SECTION_KEYS: tuple[str, ...] = (
    "overview", "dispatch", "routes", "vehicles", "drivers", "appeals",
    "alerts", "performance", "top-performance", "electricity", "rejects",
    "brutto", "tariffs", "insights", "audit", "myai", "gps", "video",
    "sms", "admin", "logs", "disk", "documents", "users", "settings",
    "dusers",
)

SECTION_LABELS: dict[str, str] = {
    "overview": "Umumiy",
    "dispatch": "Dispatch",
    "routes": "Yo'nalishlar",
    "vehicles": "Avtobuslar",
    "drivers": "Haydovchilar",
    "appeals": "Murojaatlar",
    "alerts": "Ogohlantirishlar",
    "performance": "Ko'rsatkichlar",
    "top-performance": "Top natijalar",
    "electricity": "Elektr",
    "rejects": "Qabul qilinmagan",
    "brutto": "Brutto",
    "tariffs": "Tariflar",
    "insights": "AI tahlil",
    "audit": "Audit",
    "myai": "MyAI",
    "gps": "GPS",
    "video": "Video",
    "sms": "SMS",
    "admin": "Admin panel",
    "logs": "Loglar",
    "disk": "Disk",
    "documents": "Xujjatlar",
    "users": "Xodimlar",
    "settings": "Sozlamalar",
    "dusers": "Foydalanuvchilar",
}

# Bot buyruq/nav nomi → bo'lim kaliti. Ro'yxatda yo'q buyruqlar (help,
# start, myrole, cancel, login, haydovchi oqimlari) doim ochiq.
BOT_SECTIONS: dict[str, str] = {
    # Asosiy ko'rsatkichlar / filtr
    "today": "overview",
    "dashboard": "overview",
    "month": "overview",
    "monthly": "overview",
    "daily": "overview",
    "status": "overview",
    "profiles": "overview",          # firma tanlash — filtr, bo'lim emas
    # Park
    "routes": "routes",
    "vehicles": "vehicles",
    "vehicle": "vehicles",
    "drivers": "drivers",
    "driver": "drivers",
    "top": "top-performance",
    "leaderboard": "top-performance",
    "distance": "performance",
    "attendance": "performance",
    "reports": "performance",
    "export": "performance",
    "errors": "logs",
    # Dispatch / reyslar
    "trips": "dispatch",
    "schedule": "dispatch",
    "plan": "dispatch",
    "sync": "dispatch",
    "syncmonthly": "dispatch",
    "resend": "dispatch",
    "grafik": "dispatch",
    "grafik_today": "dispatch",
    "grafik_tomorrow": "dispatch",
    "grafik_yesterday": "dispatch",
    "notify": "dispatch",
    "alerts": "alerts",
    "problems": "alerts",
    # Murojaatlar
    "murojaat": "appeals",
    "murojaatlarim": "appeals",
    # Moliya (qo'shimcha ravishda `salary` amali bilan yopiladi)
    "brutto": "brutto",
    "hisob": "drivers",
    "salary_dl": "drivers",
    "verify": "drivers",
    "finesreport": "drivers",
    # AI
    "ai": "insights",
    "sohbat": "insights",
    "suhbat": "insights",
    "chat": "insights",
    "myai": "myai",
    "insights": "audit",
    # Sozlamalar / boshqaruv
    "settings": "settings",
    "addcompany": "settings",
    "users": "users",
    "whois": "users",
    "setrole": "dusers",
    "role": "dusers",
    # Xujjatlar
    "doc": "documents",
    "docs": "documents",
    "list": "documents",
}

# Bo'limlar ro'yxati berilmagan (`None`) bo'lsa — faqat rol bo'yicha ishlaydi.
UNRESTRICTED: None = None


def section_for_command(name: str) -> str:
    """Buyruq/nav nomiga mos bo'lim kaliti (topilmasa bo'sh satr)."""
    return BOT_SECTIONS.get(str(name or "").strip().lower().lstrip("/"), "")


def section_label(key: str) -> str:
    """Bo'limning o'zbekcha nomi (noma'lum bo'lsa kalit o'zi)."""
    return SECTION_LABELS.get(str(key or ""), str(key or ""))


def clean_sections(raw) -> tuple[list[str] | None, str]:
    """Payload'dagi bo'limlar ro'yxatini tekshiradi -> (ro'yxat|None, xato).

    `None`/`""` -> (None, ""): cheklov yo'q (rol bo'yicha, eski xatti-harakat).
    Noma'lum kalit jimgina tashlanmaydi — xato qaytariladi.
    """
    if raw is None or raw == "":
        return None, ""
    if not isinstance(raw, (list, tuple)):
        return None, "Bo'limlar ro'yxati (array) bo'lishi kerak"
    out: list[str] = []
    bad: list[str] = []
    for item in raw:
        key = str(item or "").strip()
        if not key:
            continue
        if key not in SECTION_KEYS:
            bad.append(key)
        elif key not in out:
            out.append(key)
    if bad:
        return None, "Noma'lum bo'lim: " + ", ".join(sorted(set(bad))[:5])
    return out, ""


def sections_allowed(pages: list[str] | None, section: str) -> bool:
    """Ro'yxat `None` (cheklovsiz) yoki bo'lim ichida bo'lsa — ruxsat."""
    if not section:
        return True
    if pages is None:
        return True
    return str(section) in {str(p) for p in pages}


def section_denied_text(section: str) -> str:
    """Bo'lim yopiq bo'lganda yuboriladigan xabar (o'zbekcha)."""
    return (f"⛔ «{section_label(section)}» bo'limiga ruxsat berilmagan.\n"
            "Administrator ruxsat sozlamasini o'zgartirishi mumkin.")
