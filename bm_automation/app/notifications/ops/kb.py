"""Klaviaturalar: inline navigatsiya va doimiy (reply) menyu.

Inline tugmalar (katalog bo'yicha):
📊 Dashboard · 🚌 Avtobuslar · 👨‍✈️ Haydovchilar · 🛣 Yo'nalishlar ·
📋 Reyslar · ⚠️ Muammolar · 📈 Hisobot · 🔄 Sync · ⚙️ Settings
"""

from __future__ import annotations

NAV_ITEMS = [
    ("📊 Dashboard", "nav:dashboard"),
    ("🚌 Avtobuslar", "nav:vehicles"),
    ("👨‍✈️ Haydovchilar", "nav:drivers"),
    ("🛣 Yo'nalishlar", "nav:routes"),
    ("📋 Reyslar", "nav:trips"),
    ("⚠️ Muammolar", "nav:problems"),
    ("📈 Hisobot", "nav:reports"),
    ("🔄 Sync", "nav:sync"),
    ("🏢 Firmalar", "nav:profiles"),
    ("⚙️ Settings", "nav:settings"),
]

# Reply-menu tugmalari (doimiy) — matni -> command xaritasi ops/dispatch'da
MAIN_MENU_ROWS = [
    ["📊 Dashboard", "🚌 Avtobuslar"],
    ["👨‍✈️ Haydovchilar", "🛣 Yo'nalishlar"],
    ["📋 Reyslar", "⚠️ Muammolar"],
    ["📈 Hisobot", "🔄 Sync"],
    ["🏢 Firmalar", "⚙️ Settings", "❓ Yordam"],
]


def _row(buttons: list[tuple[str, str]], per_row: int = 3) -> list[list[dict]]:
    return [
        [{"text": label, "callback_data": data} for label, data in buttons[i:i + per_row]]
        for i in range(0, len(buttons), per_row)
    ]


def nav_kb(*exclude: str) -> dict:
    """Asosiy inline navigatsiya klaviaturasi (3 ustunli)."""
    items = [(t, d) for t, d in NAV_ITEMS if d not in exclude]
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


def sync_confirm_kb() -> dict:
    """Sync tasdiqlash tugmalari."""
    return {
        "inline_keyboard": [
            [{"text": "✅ Ha, bajarsin", "callback_data": "sync:confirm"},
             {"text": "❌ Yo'q", "callback_data": "sync:cancel"}]
        ]
    }


def profiles_kb(profiles: list[dict], active: str = "") -> dict:
    """Kompaniyalarni tanlash klaviaturasi (bir ustun)."""
    rows = []
    for p in profiles:
        label = p.get("label") or p.get("name") or "-"
        data = f"prof:{p['name']}"
        text = label
        if p.get("route_name"):
            text += f" · {p['route_name']}"
        if p.get("name") == active:
            text = f"✅ {text}"
        rows.append([{"text": text, "callback_data": data[:64]}])
    if active:
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


def main_menu_kb() -> dict:
    """Doimiy reply-klaviatura (asosiy menyu)."""
    return {
        "keyboard": MAIN_MENU_ROWS,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }
