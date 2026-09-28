"""Klaviaturalar: inline navigatsiya va doimiy (reply) menyu.

Inline tugmalar (katalog bo'yicha):
📊 Dashboard · 🚌 Avtobuslar · 👨‍✈️ Haydovchilar · 🛣 Yo'nalishlar ·
📋 Reyslar · ⚠️ Muammolar · 📈 Hisobot · 🔄 Sync · ⚙️ Settings
"""

from __future__ import annotations

from datetime import date

from ...utils.logger import get_logger

log = get_logger("bm_automation.bot")

NAV_ITEMS = [
    ("📊 Dashboard", "nav:dashboard"),
    ("📅 Oy", "nav:month"),
    ("🧾 Brutto", "nav:brutto"),
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
    ["📅 Kechagi grafik", "📅 Bugungi grafik", "📅 Ertagi grafik"],
    ["🏆 Reyting", "📏 Masofa", "📋 Davomat"],
    ["🤖 AI yordamchi", "🧠 MyAI", "🗓 Reja"],
    ["🧾 Brutto", "💰 Oylik", "🧠 O'zini tahlil"],
    ["💬 Suhbat", "⚙️ Settings", "❓ Yordam"],
]

# Ruscha menyu (til tanlash /setlang orqali yoqiladi)
MAIN_MENU_RU_ROWS = [
    ["📊 Дашборд", "📅 Месяц", "⚠️ Проблемы"],
    ["🛣 Маршруты", "🚌 Автобусы", "👨‍✈️ Водители"],
    ["📋 Рейсы", "📈 Отчёт", "🏢 Компании"],
    ["📅 График вчера", "📅 График сегодня", "📅 График завтра"],
    ["🏆 Рейтинг", "📏 Расстояние", "📋 Посещаемость"],
    ["🤖 AI помощник", "🧠 MyAI", "🗓 План"],
    ["🧾 Брутто", "💰 Зарплата", "🧠 Самоанализ"],
    ["💬 Диалог", "⚙️ Настройки", "❓ Помощь"],
]


def _row(buttons: list[tuple[str, str]], per_row: int = 3) -> list[list[dict]]:
    return [
        [{"text": label, "callback_data": data} for label, data in buttons[i:i + per_row]]
        for i in range(0, len(buttons), per_row)
    ]


# Telegram callback_data uzunligi 64 bayt bilan cheklangan. `[:64]` kabi
# tinimsiz kesish id'ni jim bildirib buzardi (masalan dsched:xxx:1).
# `_cb` kesish o'rniga xato ko'taradi — noto'g'ri id bilan tugma yuborilmaydi.

def _cb(prefix: str, *parts: str) -> str:
    data = prefix + "".join(parts)
    if len(data.encode("utf-8", "ignore")) <= 64:
        return data
    raise ValueError(f"callback_data uzun (>64 bayt): {data[:48]}...")


# Haydovchilar faqat o'z kartasi (Dashboard) va Settings'ga kirishadi —
# kompaniya miqyosidagi bo'limlar (muammolar, reyslar, firma va h.k.) yashirin.
_DRIVER_ALLOW_NAVS = {"nav:dashboard", "nav:settings"}


def nav_kb(*exclude: str, chat_id: int | None = None) -> dict:
    """Asosiy inline navigatsiya klaviaturasi (3 ustunli).

    Haydovchilar uchun faqat Dashboard va Settings ko'rsatiladi.
    """
    from .roles import Role, resolve_role
    is_driver = chat_id is not None and resolve_role(chat_id) is Role.DRIVER
    items = []
    for text, data in NAV_ITEMS:
        if data in exclude:
            continue
        if is_driver and data not in _DRIVER_ALLOW_NAVS:
            continue
        items.append((text, data))
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
        try:
            data = _cb("d:", did)
        except ValueError as exc:
            log.warning("haydovchi tugmasi o'tkazib yuborildi: %s", exc)
            continue
        buttons.append((f"{mark}{r.get('name') or did}", data))
    return {"inline_keyboard": _row(buttons, per_row=2)} if buttons else None


def driver_card_kb(driver_id: str) -> dict:
    """Haydovchi karta tugmalari: kunlik qayd va jarima kiritish."""
    return {
        "inline_keyboard": [[
            {"text": "📝 Kunlik qayd",
             "callback_data": _cb("dlog:", driver_id)},
            {"text": "⚠️ Jarima",
             "callback_data": _cb("dfine:", driver_id)},
        ]]
    }


def driver_schedule_kb(driver_id: str) -> dict:
    """Haydovchi kartasidagi grafik tugmalari — kechagi/bugungi/ertangi."""
    return {
        "inline_keyboard": [
            [{"text": "🗓 Grafikim (ertaga)",
              "callback_data": _cb("dsched:", driver_id, ":1")}],
            [
                {"text": "⬅️ Kechagi", "callback_data": _cb("dsched:", driver_id, ":-1")},
                {"text": "Bugun", "callback_data": _cb("dsched:", driver_id, ":0")},
            ],
        ]
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
        try:
            data = _cb("v:", vid)
        except ValueError as exc:
            log.warning("avtobus tugmasi o'tkazib yuborildi: %s", exc)
            continue
        buttons.append((f"{mark}{label}", data))
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


APPEAL_TOPICS: tuple[str, ...] = (
    "🚧 Yo'l muammosi",
    "💰 Maosh / ish haqi",
    "🚌 Texnika / avtobus",
    "🗓 Grafik / reyslar",
    "🛡 Xavfsizlik",
    "💬 Taklif",
    "🔧 Boshqa",
)


def appeal_topics_kb() -> dict:
    """Murojaat mavzusi tanlash tugmalari (to'liq oqim uchun)."""
    rows = []
    for i in range(0, len(APPEAL_TOPICS), 2):
        chunk = APPEAL_TOPICS[i:i + 2]
        rows.append([
            {"text": t, "callback_data": f"apptopic:{i + j}"}
            for j, t in enumerate(chunk)
        ])
    rows.append([{"text": "➡️ Mavzusiz", "callback_data": "apptopic:none"},
                 {"text": "❌ Bekor qilish", "callback_data": "dentry:cancel"}])
    return {"inline_keyboard": rows}


def driver_entry_cancel_kb() -> dict:
    """Kunlik qayd / jarima / murojaat dialogini bekor qilish tugmasi."""
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
        data = _cb("pr:", route_id) if route_id else _cb("prof:", p["name"])
        text = label
        if route_name:
            text += f" · {route_name}"
        if p.get("name") == active:
            text = f"✅ {text}"
        rows.append([{"text": text, "callback_data": data}])
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
    from .roles import Role, resolve_role, driver_id_for_chat
    is_driver = False
    unlinked = False
    if chat_id is not None:
        try:
            role = resolve_role(chat_id)
            is_driver = role is Role.DRIVER
            # Bog'lanmagan oddiy foydalanuvchi (VIEWER) — haydovchi bo'lishi
            # mumkin: menyuga "Raqamni ulashish" tugmasi qo'shiladi.
            unlinked = (role is Role.VIEWER
                        and not driver_id_for_chat(chat_id))
        except Exception:
            pass
    if is_driver:
        rows = [["📊 Dashboard", "📬 Murojaat"], ["⚙️ Settings", "❓ Yordam"]]
        ru_rows = [["📊 Дашборд", "📬 Обращение"], ["⚙️ Настройки", "❓ Помощь"]]
        if chat_id is not None:
            try:
                from ...core.bot_settings import lang
                if lang(chat_id) == "ru":
                    rows = ru_rows
            except Exception:  # noqa: BLE001 - til aniqlanmasa o'zbekcha qoladi
                pass
        return {
            "keyboard": rows,
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }
    if chat_id is not None:
        try:
            from ...core.bot_settings import lang
            if lang(chat_id) == "ru":
                kb_ = {
                    "keyboard": MAIN_MENU_RU_ROWS,
                    "resize_keyboard": True,
                    "one_time_keyboard": False,
                }
                if unlinked:
                    kb_["keyboard"] = kb_["keyboard"] + [
                        [{"text": "📱 Поделиться номером",
                          "request_contact": True}]]
                return kb_
        except Exception:  # noqa: BLE001 - til aniqlanmasa o'zbekcha qoladi
            pass
    kb_ = {
        "keyboard": MAIN_MENU_ROWS,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }
    if unlinked:
        kb_["keyboard"] = kb_["keyboard"] + [
            [{"text": "📱 Raqamni ulashish", "request_contact": True}]]
    return kb_


def share_phone_kb() -> dict:
    """Haydovchi telefon raqamini ulashish tugmasi (request_contact)."""
    return {"keyboard": [[{"text": "📱 Raqamni ulashish",
                           "request_contact": True}]],
            "resize_keyboard": True, "one_time_keyboard": True}


def settings_kb(chat_id: int | None = None) -> dict:
    """Settings ekrani tugmalari: narxlar (ADMIN/DISPATCHER/MANAGER) + til tanlash."""
    from .roles import Role, can, resolve_role
    role = resolve_role(chat_id) if chat_id is not None else Role.ADMIN
    rows = []
    if role in (Role.ADMIN, Role.DISPATCHER, Role.MANAGER):
        rows.append([{"text": "💵 1 km narxi o'zgartirish",
                      "callback_data": "settings:kmrate"}])
    if can(role, "salary"):
        from . import context
        cur = (context.filters_for(chat_id) or {}).get("route") or ""
        if cur:
            rows.append([{"text": "🏢 Joriy firma SKM o'zgartirish",
                          "callback_data": "settings:rskm"}])
        rows.append([{"text": "⚡ Elektr narxi o'zgartirish",
                      "callback_data": "settings:elecprice"}])
        rows.append([{"text": "📋 SKM (116-son) o'zgartirish",
                      "callback_data": "settings:skm"}])
        rows.append([{"text": "🕐 Sozlama tarixi", "callback_data": "settings:audit"}])
    if can(role, "driver_edit"):
        rows.append([{"text": "🔗 Haydovchi bog'lash (izlash)",
                      "callback_data": "settings:linkdriver"}])
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
