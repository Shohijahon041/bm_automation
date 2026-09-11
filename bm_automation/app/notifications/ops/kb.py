"""Klaviaturalar: inline navigatsiya va doimiy (reply) menyu.

Inline tugmalar (katalog bo'yicha):
📊 Dashboard · 🚌 Avtobuslar · 👨‍✈️ Haydovchilar · 🛣 Yo'nalishlar ·
📋 Reyslar · ⚠️ Muammolar · 📈 Hisobot · 🔄 Sync · ⚙️ Settings
"""

from __future__ import annotations

from datetime import date

NAV_ITEMS = [
    ("📊 Dashboard", "nav:dashboard"),
    ("📅 Oy", "nav:month"),
    ("🚌 Avtobuslar", "nav:vehicles"),
    ("👨‍✈️ Haydovchilar", "nav:drivers"),
    ("🛣 Yo'nalishlar", "nav:routes"),
    ("📋 Reyslar", "nav:trips"),
    ("⚠️ Muammolar", "nav:problems"),
    ("📈 Hisobot", "nav:reports"),
    ("🔄 Sync", "nav:sync"),
    ("🗓 Reja", "nav:plan"),
    ("🏢 Firmalar", "nav:profiles"),
    ("⚙️ Settings", "nav:settings"),
    ("🧠 O'zini tahlil", "nav:insights"),
]

# Reply-menu tugmalari (doimiy) — matni -> command xaritasi ops/dispatch'da
MAIN_MENU_ROWS = [
    ["📊 Dashboard", "📅 Oy", "⚠️ Muammolar"],
    ["🛣 Yo'nalishlar", "🚌 Avtobuslar", "👨‍✈️ Haydovchilar"],
    ["📋 Reyslar", "📈 Hisobot", "🏢 Firmalar"],
    ["🖼 Grafik yuborish", "⚙️ Settings", "❓ Yordam"],
    ["🏆 Reyting", "📏 Masofa", "📋 Davomat"],
    ["🤖 AI yordamchi", "🧠 MyAI", "🗓 Reja"],
    ["💰 Oylik", "🧠 O'zini tahlil", "💬 Suhbat"],
]

# Ruscha menyu (til tanlash /setlang orqali yoqiladi)
MAIN_MENU_RU_ROWS = [
    ["📊 Дашборд", "📅 Месяц", "⚠️ Проблемы"],
    ["🛣 Маршруты", "🚌 Автобусы", "👨‍✈️ Водители"],
    ["📋 Рейсы", "📈 Отчёт", "🏢 Компании"],
    ["🖼 Отправить график", "⚙️ Настройки", "❓ Помощь"],
    ["🏆 Рейтинг", "📏 Расстояние", "📋 Посещаемость"],
    ["🤖 AI помощник", "🧠 MyAI", "🗓 План"],
    ["💰 Зарплата", "🧠 Самоанализ", "💬 Диалог"],
]


def _row(buttons: list[tuple[str, str]], per_row: int = 3) -> list[list[dict]]:
    return [
        [{"text": label, "callback_data": data} for label, data in buttons[i:i + per_row]]
        for i in range(0, len(buttons), per_row)
    ]


_DRIVER_EXCLUDE_NAVS = {"nav:sync", "nav:plan", "nav:insights"}


def nav_kb(*exclude: str, chat_id: int | None = None) -> dict:
    """Asosiy inline navigatsiya klaviaturasi (3 ustunli).

    Haydovchilar uchun sync/reja/insights yashiriladi.
    """
    from .roles import Role, resolve_role
    is_driver = chat_id is not None and resolve_role(chat_id) is Role.DRIVER
    items = [(t, d) for t, d in NAV_ITEMS if d not in exclude
             and not (is_driver and d in _DRIVER_EXCLUDE_NAVS)]
    return {"inline_keyboard": _row(items)}


def problems_kb(problems: dict) -> dict:
    """Muammo kategoriyalari tugmalari (bosilganda batafsil ochiladi)."""
    labels = {
        "gps": "GPS",
        "technical": "texnik",
        "schedule": "jadval",
        "unknown": "noma'lum",
    }
    counts = problems.get("counts", {})
    buttons = []
    for key in ("gps", "technical", "schedule", "unknown"):
        n = int(counts.get(key, 0) or 0)
        buttons.append((f"⚠️ {labels[key]} ({n})", f"prob:{key}"))
    return {"inline_keyboard": _row(buttons, per_row=2)}


def back_kb(target: str, label: str = "◀️ Orqaga") -> dict:
    """Orqaga qaytish tugmasi (inline)."""
    return {"inline_keyboard": [[{"text": label, "callback_data": target}]]}


def driver_buttons(rows: list[dict], limit: int = 8) -> dict | None:
    """Haydovchilar ro'yxatidan karta ochish tugmalari (2 ustunli).

    Har bir tugma ``d:<driver_id>`` callback bilan karta ochadi. Qora
    ro'yxatdagilar ⛔ belgi bilan ko'rsatiladi.
    """
    buttons = []
    for r in (rows or [])[:limit]:
        did = r.get("driver_id") or ""
        if not did:
            continue
        mark = "⛔ " if r.get("blacklisted") else ""
        buttons.append((f"{mark}{r.get('name') or did}", f"d:{did}"[:64]))
    return {"inline_keyboard": _row(buttons, per_row=2)} if buttons else None


def driver_card_kb(driver_id: str) -> dict:
    """Haydovchi karta tugmalari: kunlik qayd va jarima kiritish."""
    return {
        "inline_keyboard": [[
            {"text": "📝 Kunlik qayd", "callback_data": f"dlog:{driver_id}"[:64]},
            {"text": "⚠️ Jarima", "callback_data": f"dfine:{driver_id}"[:64]},
        ]]
    }


def vehicle_buttons(rows: list[dict], limit: int = 8) -> dict | None:
    """Avtobuslar ro'yxatidan karta ochish tugmalari (2 ustunli).

    Har bir tugma ``v:<vehicle_id>`` callback bilan karta ochadi. Faol
    avtobuslar 🔄 belgi bilan ko'rsatiladi.
    """
    buttons = []
    for r in (rows or [])[:limit]:
        vid = r.get("vehicle_id") or ""
        if not vid:
            continue
        label = r.get("plate_number") or vid
        mark = "🔄 " if r.get("status") == "faol" else "❌ "
        buttons.append((f"{mark}{label}", f"v:{vid}"[:64]))
    return {"inline_keyboard": _row(buttons, per_row=2)} if buttons else None


def download_kb(scope: str) -> dict:
    """Eksport tugmalari (CSV / XLSX) — ekranlardagi yuklab olish."""
    return {"inline_keyboard": [
        [{"text": "📥 CSV", "callback_data": f"dl:csv:{scope}"},
         {"text": "📥 XLSX", "callback_data": f"dl:xlsx:{scope}"}]
    ]}


def salary_dl_kb() -> dict:
    """Oylik yuklab olish — sana oralig'ini tanlash klaviaturasi."""
    today = date.today()
    y, m = today.year, today.month
    days_in_month = (date(y, m + 1, 1) - date(y, m, 1)).days if m < 12 else 31
    # O'tgan oy
    pm = m - 1 if m > 1 else 12
    py = y if m > 1 else y - 1
    return {"inline_keyboard": [
        [{"text": f"📅 Bu oy (1-{today.day})", "callback_data": f"sal:{y:04d}-{m:02d}-01:{y:04d}-{m:02d}-{today.day:02d}"},
         {"text": f"📅 O'tgan oy", "callback_data": f"sal:{py:04d}-{pm:02d}-01:{py:04d}-{pm:02d}-{(date(py, pm+1, 1)-date(py, pm, 1)).days if pm < 12 else 31:02d}"}],
        [{"text": f"📅 1-15", "callback_data": f"sal:{y:04d}-{m:02d}-01:{y:04d}-{m:02d}-15"},
         {"text": f"📅 16-{days_in_month}", "callback_data": f"sal:{y:04d}-{m:02d}-16:{y:04d}-{m:02d}-{days_in_month:02d}"}],
        [{"text": "✏️ Boshqa sana", "callback_data": "sal:custom"}],
    ]}


def driver_entry_cancel_kb() -> dict:
    """Kunlik qayd / jarima dialogini bekor qilish tugmasi."""
    return {"inline_keyboard": [
        [{"text": "❌ Bekor qilish", "callback_data": "dentry:cancel"}],
    ]}


def sync_confirm_kb() -> dict:
    """Sync tasdiqlash tugmalari."""
    return {
        "inline_keyboard": [
            [{"text": "✅ Ha, bajarsin", "callback_data": "sync:confirm"},
             {"text": "❌ Yo'q", "callback_data": "sync:cancel"}]
        ]
    }


def sync_monthly_confirm_kb() -> dict:
    """Oylik sync tasdiqlash tugmalari."""
    return {
        "inline_keyboard": [
            [{"text": "✅ Ha, bajarsin", "callback_data": "sync:monthly_confirm"},
             {"text": "❌ Yo'q", "callback_data": "sync:monthly_cancel"}]
        ]
    }


def profiles_kb(profiles: list[dict], active: str = "", allow_all: bool = False) -> dict:
    """Kompaniyalarni tanlash klaviaturasi (bir ustun).

    allow_all=True bo'lsa 'Barchasi / Chiqish' tugmasi qo'shiladi (faqat
    ADMIN barcha kompaniyalarni ko'ra olgani uchun).
    Agar bir nechta yo'nalish bo'lsa, '🌐 Hammasi' tugmasi qo'shiladi.
    """
    rows = []
    for p in profiles:
        label = p.get("label") or p.get("name") or "-"
        route_name = p.get("route_name") or ""
        route_id = (p.get("routes") or [""])[0]
        data = f"pr:{route_id}" if route_id else f"prof:{p['name']}"
        text = label
        if route_name:
            text += f" · {route_name}"
        if p.get("name") == active:
            text = f"✅ {text}"
        rows.append([{"text": text, "callback_data": data[:64]}])
    unique_names = list(dict.fromkeys(p.get("name", "") for p in profiles))
    if len(profiles) > 1 and len(unique_names) <= 1:
        rows.append([{"text": "🌐 Hammasi (barcha yo'nalishlar)",
                      "callback_data": "prof:__all_routes__"}])
    if active and allow_all:
        rows.append([{"text": "🌐 Barchasi / Chiqish",
                      "callback_data": "prof:__all__"}])
    return {"inline_keyboard": rows}


def profile_bar(name: str) -> dict:
    """Faol kompaniya belgisi + chiqish tugmasi (ekranlarda)."""
    return {
        "inline_keyboard": [[
            {"text": f"🏢 {name} · Chiqish", "callback_data": "prof:__all__"},
        ]]
    }


def main_menu_kb(chat_id: int | None = None) -> dict:
    """Doimiy reply-klaviatura (asosiy menyu).

    chat_id berilgan bo'lsa foydalanuvchi tili ruscha (ru) bo'lsa ruscha
    menyu ko'rsatiladi. Haydovchilar uchun cheklangan menyu.
    """
    from .roles import Role, resolve_role
    is_driver = False
    if chat_id is not None:
        try:
            role = resolve_role(chat_id)
            is_driver = role is Role.DRIVER
        except Exception:
            pass
    if is_driver:
        return {
            "keyboard": [
                ["📊 Dashboard", "👨‍✈️ Haydovchilar", "🛣 Yo'nalishlar"],
                ["📋 Reyslar", "📅 Oy", "📈 Hisobot"],
                ["🏆 Reyting", "📏 Masofa", "❓ Yordam"],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }
    if chat_id is not None:
        try:
            from ...core.bot_settings import lang
            if lang(chat_id) == "ru":
                return {
                    "keyboard": MAIN_MENU_RU_ROWS,
                    "resize_keyboard": True,
                    "one_time_keyboard": False,
                }
        except Exception:  # noqa: BLE001 - til aniqlanmasa o'zbekcha qoladi
            pass
    return {
        "keyboard": MAIN_MENU_ROWS,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def settings_kb(chat_id: int | None = None) -> dict:
    """Settings ekrani tugmalari: km narxi (ADMIN/DISPATCHER/MANAGER) + til tanlash."""
    from .roles import Role, resolve_role
    role = resolve_role(chat_id) if chat_id is not None else Role.ADMIN
    rows = []
    if role in (Role.ADMIN, Role.DISPATCHER, Role.MANAGER):
        rows.append([{"text": "💵 1 km narxi o'zgartirish",
                      "callback_data": "settings:kmrate"}])
    rows.append([{"text": "🌐 Til tanlash", "callback_data": "settings:lang"}])
    return {"inline_keyboard": rows}


def lang_kb(current: str = "uz") -> dict:
    """Til tanlash tugmalari (uz / ru)."""

    def _label(code: str) -> str:
        mark = "✅ " if code == current else ""
        if code == "ru":
            return f"{mark}🇷🇺 Русский"
        return f"{mark}🇺🇿 O'zbek"

    return {
        "inline_keyboard": [
            [{"text": _label("uz"), "callback_data": "setlang:uz"},
             {"text": _label("ru"), "callback_data": "setlang:ru"}],
        ]
    }


def plan_kb() -> dict:
    """Ertangi reja ekrani tugmalari."""
    return {
        "inline_keyboard": [
            [{"text": "➕ Haydovchi qo'shish", "callback_data": "plan:add"},
             {"text": "🗑 Tozalash", "callback_data": "plan:clear"}],
        ]
    }


def plan_cancel_kb() -> dict:
    """Rejalashtirish dialogini bekor qilish tugmasi."""
    return {"inline_keyboard": [
        [{"text": "❌ Bekor qilish", "callback_data": "plan:cancel"}],
    ]}
