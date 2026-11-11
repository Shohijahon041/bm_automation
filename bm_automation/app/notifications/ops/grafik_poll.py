"""Haydovchilar guruhi uchun kunlik grafikni tasdiqlash so'rovnomasi (poll).

BM saytida (bm.dtransport.uz) grafik e'lon qilinganda yo'nalish guruhiga
avtomatik so'rovnoma yuboriladi (yoki admin/dispetcher tomonidan /sorov orqali).
Haydovchilar o'z grafigini tasdiqlab, ertaga ishga chiqishini isbotlaydi.
Tasdiqlashlar real vaqtda xabarning o'zida (in-place) yangilanadi.

Tugmalar:
  - "⚡️ O'z grafikimni tasdiqlash" — telegram id orqali tezkor tasdiqlash;
  - "⏳ [Grafik]: [Ism]" — ro'yxatdan o'z grafigini tanlash va tasdiqlash
    (bir vaqtning o'zida haydovchi telegram_chat_id bazaga bog'lanadi);
  - "🔄 Yangilash" — holatni qayta yuklash.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from ...config.settings import telegram_settings
from ...db.storage import get_storage
from ...utils.io import atomic_write
from ...utils.names import short_name
from ..telegram import telegram_call

log = logging.getLogger("bm_automation.grafik_poll")

STATE_FILE = Path("state") / "grafik_polls.json"
_LOCK = threading.Lock()

WEEKDAYS_UZ = [
    "Dushanba",
    "Seshanba",
    "Chorshanba",
    "Payshanba",
    "Juma",
    "Shanba",
    "Yakshanba",
]


def enabled() -> bool:
    """Kunlik grafik so'rovnomasi yoqilganmi?"""
    return os.getenv("GRAFIK_POLL", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _load_state() -> dict:
    """Holat faylini xavfsiz o'qiydi."""
    if not STATE_FILE.exists():
        return {"polls": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"polls": {}}
        if "polls" not in data:
            data["polls"] = {}
        return data
    except Exception as exc:
        log.warning("grafik_polls.json o'qishda xatolik: %s", exc)
        return {"polls": {}}


def _save_state(st: dict) -> None:
    """Holat fayliga atomik yozadi."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            STATE_FILE, json.dumps(st, ensure_ascii=False, indent=2)
        )
    except Exception as exc:
        log.error("grafik_polls.json saqlashda xatolik: %s", exc)


def poll_id_for(route_id: str, date_str: str) -> str:
    """Qisqa, Telegram callback limitiga (64 bayt) sig'adigan identifikator.

    Masalan: fe319a0a_20261112 (17 belgi).
    """
    clean_rid = str(route_id or "").replace("-", "")[:8]
    clean_date = str(date_str or "").replace("-", "")[:8]
    return f"{clean_rid}_{clean_date}"


def get_poll(poll_id: str) -> dict | None:
    """So'rovnoma holatini qaytaradi."""
    with _LOCK:
        st = _load_state()
        return st.get("polls", {}).get(poll_id)


def save_poll(poll: dict) -> None:
    """So'rovnoma holatini saqlaydi."""
    pid = poll.get("poll_id")
    if not pid:
        return
    with _LOCK:
        st = _load_state()
        st["polls"][pid] = poll
        _save_state(st)


def compact_driver_name(full: str) -> str:
    """Tugmalar uchun qisqa ism: 'Nishanov M.' yoki 'Raximov S.'"""
    tokens = [t for t in str(full or "").split() if t]
    if not tokens:
        return "Haydovchi"
    last = tokens[0].capitalize()
    first = tokens[1][0].upper() + "." if len(tokens) > 1 else ""
    return f"{last} {first}".strip()


def get_bot_username() -> str:
    """Bot @username ni qaytaradi (keshlangan yoki env)."""
    try:
        from .ops import _bot_username_cached
        u = _bot_username_cached()
        if u:
            return u
    except Exception:
        pass
    uname = os.getenv("TG_BOT_USERNAME", "").strip().lstrip("@")
    return uname or "vge_haydovchi_bot"


def get_bot_link_url() -> str:
    """Haydovchi o'z profilini botga bog'lashi uchun Telegram havolasi."""
    uname = get_bot_username()
    return f"https://t.me/{uname}?start=link"


def get_bound_driver_info(user_id: int | None) -> tuple[str | None, str]:
    """Telegram user_id ga bog'langan driver_id va haydovchi ismini qaytaradi.

    Qaytaradi: (driver_id, driver_display_name). Agar topilmasa: (None, "").
    """
    if not user_id:
        return None, ""

    did = None
    try:
        from .roles import driver_id_for_chat
        did = driver_id_for_chat(user_id)
    except Exception:
        did = None

    if not did:
        try:
            st = get_storage()
            if st and st.enabled:
                prof = st.find_driver_by_telegram(user_id)
                if prof:
                    did = str(prof.get("driver_id") or "").strip() or None
                    if did:
                        try:
                            from .roles import remember_driver_chat
                            remember_driver_chat(user_id, did)
                        except Exception:
                            pass
        except Exception:
            did = None

    if not did:
        return None, ""

    dname = ""
    try:
        st = get_storage()
        if st and st.enabled:
            drow = st.find("drivers", external_id=did)
            if drow:
                full = str(drow.get("full_name") or "")
                dname = compact_driver_name(full)
    except Exception:
        pass

    if not dname:
        dname = f"ID:{did[:8]}"

    return did, dname


def _normalize_name(name: str | None) -> str:
    """Ismni solishtirish uchun normallashtiradi."""
    if not name:
        return ""
    s = str(name).strip().upper()
    for ch in ("‘", "’", "`", "'", "ʻ", "ʼ"):
        s = s.replace(ch, "'")
    return " ".join(s.split())


def build_poll_data(
    route_id: str,
    target_date: date | str,
    graphs: list | None = None,
    route_name: str = "",
) -> dict:
    """Yo'nalish va sana bo'yicha so'rovnoma ma'lumotlarini tayyorlaydi.

    Duty ma'lumoti berilmagan bo'lsa BM API yoki bazadagi `schedules`
    jadvalidan olinadi. Foydalanuvchi avval tasdiqlagan bo'lsa, uning
    tasdig'i grafik o'zgarsa ham o'z kuchida qoladi.
    """
    if isinstance(target_date, str):
        d_str = target_date
        try:
            d_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            d_obj = date.today()
    else:
        d_obj = target_date
        d_str = target_date.isoformat()

    pid = poll_id_for(route_id, d_str)
    existing = get_poll(pid) or {}

    # Avvalgi tasdiqlarni ko'p qirrali indekslaymiz (foydalanuvchi tasdig'i o'z kuchida qolishi uchun)
    confirmed_by_did: dict[str, dict] = {}
    confirmed_by_name: dict[str, dict] = {}
    confirmed_by_graph: dict[str, dict] = {}

    for item in existing.get("items", []):
        if not item.get("confirmed"):
            continue
        did = str(item.get("driver_id") or "").strip()
        if did:
            confirmed_by_did[did] = item
        dname = _normalize_name(item.get("driver_name"))
        if dname:
            confirmed_by_name[dname] = item
        g_name = str(item.get("graph") or "").strip()
        if g_name:
            confirmed_by_graph[g_name] = item

    if graphs is None:
        graphs = _fetch_duty_graphs(route_id, d_str)

    items = []
    used_did: set[str] = set()
    used_name: set[str] = set()
    used_graph: set[str] = set()

    for idx, g in enumerate(graphs or []):
        g_name = str(g.get("graphName") or g.get("graph_name") or (idx + 1)).strip()
        driver_id = str(g.get("driverId") or g.get("driver_id") or "").strip()
        driver_name = str(g.get("driverName") or g.get("driver_name") or "").strip()
        norm_name = _normalize_name(driver_name)
        plate = str(g.get("plateNum") or g.get("plate_number") or g.get("plate") or "").strip()
        start_time = str(g.get("startTime") or g.get("start_time") or "").strip()[:5]
        end_time = str(g.get("endTime") or g.get("end_time") or "").strip()[:5]

        # Foydalanuvchi avval tasdiqlagan bo'lsa, topamiz:
        prev = None

        # 1-ustunlik: driver_id bo'yicha (eng aniq unikal ID)
        if driver_id and driver_id in confirmed_by_did and driver_id not in used_did:
            prev = confirmed_by_did[driver_id]
            used_did.add(driver_id)
            if norm_name:
                used_name.add(norm_name)

        # 2-ustunlik: Haydovchi to'liq ismi bo'yicha (agar driver_id bo'sh yoki yangi bo'lsa)
        if not prev and norm_name and norm_name in confirmed_by_name and norm_name not in used_name:
            prev = confirmed_by_name[norm_name]
            used_name.add(norm_name)
            if driver_id:
                used_did.add(driver_id)

        # 3-ustunlik: Grafik nomi bo'yicha (agar o'sha haydovchi bo'lsa yoki driver_id yo'q bo'lsa)
        if not prev and g_name in confirmed_by_graph and g_name not in used_graph:
            cand = confirmed_by_graph[g_name]
            cand_did = str(cand.get("driver_id") or "").strip()
            cand_norm = _normalize_name(cand.get("driver_name"))
            if (cand_did and cand_did == driver_id) or (cand_norm and cand_norm == norm_name) or (not cand_did and not driver_id):
                prev = cand
                used_graph.add(g_name)

        if prev:
            # Foydalanuvchi tasdiqlagan — tasdig'i O'Z KUCHIDA QOLADI!
            # Yangilangan duty'dan avtobus, start/end time va grafik nomi olinadi.
            items.append({
                "idx": idx,
                "graph": g_name,
                "driver_id": driver_id or str(prev.get("driver_id") or ""),
                "driver_name": driver_name or str(prev.get("driver_name") or ""),
                "plate": plate or str(prev.get("plate") or ""),
                "start_time": start_time or str(prev.get("start_time") or ""),
                "end_time": end_time or str(prev.get("end_time") or ""),
                "confirmed": True,
                "confirmed_by_id": prev.get("confirmed_by_id"),
                "confirmed_by_name": prev.get("confirmed_by_name") or "",
                "confirmed_by_user": prev.get("confirmed_by_user") or "",
                "confirmed_at": prev.get("confirmed_at") or "",
            })
        else:
            # Yangi yoki hali tasdiqlanmagan haydovchi
            items.append({
                "idx": idx,
                "graph": g_name,
                "driver_id": driver_id,
                "driver_name": driver_name,
                "plate": plate,
                "start_time": start_time,
                "end_time": end_time,
                "confirmed": False,
                "confirmed_by_id": None,
                "confirmed_by_name": "",
                "confirmed_by_user": "",
                "confirmed_at": "",
            })

    weekday_name = WEEKDAYS_UZ[d_obj.weekday()]

    poll = {
        "poll_id": pid,
        "route_id": route_id,
        "route_name": route_name or existing.get("route_name") or route_id[:8],
        "date": d_str,
        "weekday": weekday_name,
        "chat_messages": existing.get("chat_messages", []),
        "created_at": existing.get("created_at") or datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "items": items,
    }
    save_poll(poll)
    return poll


def _fetch_duty_graphs(route_id: str, date_str: str) -> list:
    """BM API'dan yoki schedules jadvalidan duty grafiklarini oladi."""
    # 1. BM API orqali
    try:
        from ...api.client import BMClient
        from ...repositories.duty_repo import DutyRepository

        client = BMClient()
        duty = DutyRepository(client).by_date(route_id, date_str)
        graphs = duty.get("graphs") or []
        if graphs:
            return graphs
    except Exception as exc:
        log.info("BM API duty olish muvaffaqiyatsiz (%s): %s", route_id[:8], exc)

    # 2. Bazadan fallback
    try:
        st = get_storage()
        if st and st.enabled:
            rows = st.query(
                "SELECT * FROM schedules WHERE route_id = "
                + st.db.ph
                + " AND date = "
                + st.db.ph
                + " ORDER BY id ASC",
                (route_id, date_str),
            )
            if rows:
                out = []
                for r in rows:
                    raw_data = r.get("data")
                    if isinstance(raw_data, str) and raw_data.strip():
                        try:
                            d = json.loads(raw_data)
                            if isinstance(d, dict):
                                out.append(d)
                                continue
                        except Exception:
                            pass
                    out.append({
                        "graphName": r.get("graph_name"),
                        "driverId": r.get("driver_id"),
                        "startTime": r.get("start_time"),
                        "endTime": r.get("end_time"),
                    })
                return out
    except Exception as exc:
        log.warning("Schedules jadvalidan olish xatolik: %s", exc)

    return []


def render_poll_text(poll: dict) -> str:
    """Telegram xabari uchun to'liq so'rovnoma matnini shakllantiradi."""
    route = escape(str(poll.get("route_name") or "Yo'nalish"))
    d_str = str(poll.get("date") or "")
    weekday = escape(str(poll.get("weekday") or ""))
    try:
        d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
        date_formatted = d_obj.strftime("%d.%m.%Y")
    except Exception:
        date_formatted = d_str

    items = poll.get("items", [])
    total = len(items)
    confirmed_count = sum(1 for it in items if it.get("confirmed"))
    pct = int((confirmed_count / total * 100)) if total > 0 else 0

    lines = [
        "📋 <b>KUNLIK GRAFIKNI TASDIQLASH (SO'ROVNOMA)</b>",
        "",
        f"🚌 <b>Yo'nalish:</b> {route}",
        f"📅 <b>Sana:</b> {date_formatted} ({weekday})",
        f"📊 <b>Tasdiqlandi:</b> <b>{confirmed_count} / {total} ta</b> ({pct}%)",
        "",
    ]

    for it in items:
        g = escape(str(it.get("graph") or "-"))
        plate = escape(str(it.get("plate") or "-"))
        st_time = escape(str(it.get("start_time") or "--:--"))
        dname = compact_driver_name(it.get("driver_name") or "")

        if it.get("confirmed"):
            by_time = escape(str(it.get("confirmed_at") or ""))
            lines.append(
                f"🟢 <b>Grafik {g}</b> | <code>{plate}</code> | {st_time}\n"
                f"   👤 {escape(dname)} — ✅ <i>{by_time} da tasdiqladi</i>"
            )
        else:
            lines.append(
                f"⏳ <b>Grafik {g}</b> | <code>{plate}</code> | {st_time}\n"
                f"   👤 {escape(dname)} — <i>kutilmoqda...</i>"
            )

    lines.append("")
    if confirmed_count == total and total > 0:
        lines.append("🎉 <b>Barcha haydovchilar ishga chiqishini tasdiqladi!</b>")
    else:
        lines.append(
            "<i>💡 Haydovchilar: faqat o'z ismingiz / profilingizga bog'langan grafikni tasdiqlashingiz mumkin.\n"
            "Profilingiz hali bog'lanmagan bo'lsa, '🔗 Profilni botga bog'lash' tugmasini bosing.</i>"
        )

    return "\n".join(lines)


def render_poll_keyboard(poll: dict) -> dict:
    """Telegram inline klaviaturalarini shakllantiradi (64 bayt callback)."""
    pid = poll.get("poll_id")
    items = poll.get("items", [])
    unconfirmed = [it for it in items if not it.get("confirmed")]

    kb = []

    # 1-qator: Tezkor o'z grafigini tanlash tugmasi
    if unconfirmed:
        kb.append([
            {
                "text": "⚡️ O'z grafikimni tasdiqlash",
                "callback_data": f"gme:{pid}"[:64],
            }
        ])

        # 2-qator va keyingilari: Tasdiqlanmagan haydovchilar (qatoriga 2 tadan)
        row = []
        for it in unconfirmed[:30]:  # Telegram cheklovi uchun
            idx = it["idx"]
            g = it.get("graph") or str(idx + 1)
            name = compact_driver_name(it.get("driver_name") or "")
            btn_text = f"⏳ {g}: {name}"
            # 64 bayt limit: gck:<pid>:<idx>
            row.append({
                "text": btn_text,
                "callback_data": f"gck:{pid}:{idx}"[:64],
            })
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
    else:
        kb.append([
            {
                "text": "✅ Barcha haydovchilar tasdiqladi",
                "callback_data": f"ginfo:{pid}"[:64],
            }
        ])

    # Pastki qator: Yangilash va Profilni botga bog'lash tugmalari
    bottom_row = [
        {
            "text": "🔄 Yangilash",
            "callback_data": f"grf:{pid}"[:64],
        },
        {
            "text": "🔗 Profilni botga bog'lash",
            "url": get_bot_link_url(),
        },
    ]
    kb.append(bottom_row)

    return {"inline_keyboard": kb}


def update_poll_messages(poll: dict) -> None:
    """Guruhlardagi barcha so'rovnoma xabarlarini in-place tahrirlaydi."""
    messages = list(poll.get("chat_messages", []))
    if not messages:
        return

    text = render_poll_text(poll)
    kb = render_poll_keyboard(poll)

    for entry in messages:
        chat_id = entry.get("chat_id")
        msg_id = entry.get("message_id")
        if not chat_id or not msg_id:
            continue
        try:
            telegram_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": kb,
                },
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if "message is not modified" in err_msg:
                continue
            log.warning("Guruh xabarini tahrirlashda xatolik (%s:%s): %s",
                        chat_id, msg_id, exc)


def send_poll_to_groups(
    route_id: str,
    target_date: date | str,
    graphs: list | None = None,
    route_name: str = "",
    force: bool = False,
    specific_chat_id: int | str | None = None,
) -> dict:
    """Yo'nalishning haydovchilar guruhiga so'rovnoma yuboradi.

    `specific_chat_id` berilsa faqat shu chatga yuboradi.
    Aks holda `profiles.json` (company_groups) yoki env dagi guruhga jo'natadi.
    """
    poll = build_poll_data(
        route_id, target_date, graphs=graphs, route_name=route_name
    )

    if not poll.get("items"):
        return {"sent": 0, "error": "Grafiklar topilmadi (duty bo'sh)"}

    # Manzil guruhlarni aniqlash — faqat o'z yo'nalishiga biriktirilgan guruhga jo'natiladi.
    # Agar guruh biriktirilmagan bo'lsa, umumiy guruhlarga oqib ketmasligi shart.
    if specific_chat_id:
        targets = [str(specific_chat_id)]
    else:
        from .group_departures import _route_chats
        route_chats = _route_chats()
        targets = list(route_chats.get(route_id) or [])

    if not targets:
        log.warning(
            "So'rovnoma yuborilmadi: %s (%s) yo'nalishi uchun Telegram guruh biriktirilmagan",
            route_name,
            route_id[:8] if route_id else "-",
        )
        return {
            "sent": 0,
            "error": f"Yo'nalish ({route_name or (route_id[:8] if route_id else '')}) uchun Telegram guruh biriktirilmagan",
        }

    text = render_poll_text(poll)
    kb = render_poll_keyboard(poll)

    existing_chats = {str(m.get("chat_id")) for m in poll.get("chat_messages", [])}
    sent_count = 0
    chat_messages = list(poll.get("chat_messages", []))

    # Agar so'rovnoma xabarlari allaqachon mavjud bo'lsa, ularni eng yangi duty bilan in-place yangilaymiz
    if existing_chats:
        update_poll_messages(poll)

    for cid in targets:
        cid_str = str(cid).strip()
        if not cid_str:
            continue
        if cid_str in existing_chats and not force:
            log.info("So'rovnoma bu guruhga allaqachon yuborilgan (yangilandi): %s", cid_str)
            continue
        try:
            res = telegram_call(
                "sendMessage",
                {
                    "chat_id": cid_str,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": kb,
                },
            )
            if res and res.get("message_id"):
                chat_messages.append({
                    "chat_id": cid_str,
                    "message_id": res["message_id"],
                })
                sent_count += 1
                log.info("So'rovnoma guruhga yuborildi: chat_id=%s msg_id=%s",
                         cid_str, res["message_id"])
        except Exception as exc:
            log.error("So'rovnoma yuborishda xatolik (%s): %s", cid_str, exc)

    poll["chat_messages"] = chat_messages
    save_poll(poll)
    return {
        "sent": sent_count,
        "poll_id": poll["poll_id"],
        "total": len(targets),
        "items_count": len(poll.get("items", [])),
    }


def send_poll_for_duty(
    route_id: str,
    target_date: date | str,
    graphs: list | None = None,
    route_name: str = "",
) -> dict:
    """BM API'da grafik chiqqanda yoki yangilanganda avtomatik chaqiriladi."""
    return sync_poll_with_duty(
        route_id, target_date, graphs=graphs, route_name=route_name
    )


def sync_poll_with_duty(
    route_id: str,
    target_date: date | str,
    graphs: list | None = None,
    route_name: str = "",
) -> dict:
    """Grafik o'zgarganda so'rovnomani avtomatik yangilaydi va xabarlarni tahrirlaydi.

    Foydalanuvchi (haydovchi) avval tasdiqlagan bo'lsa, uning tasdig'i o'z kuchida saqlanadi.
    Guruhdagi xabarlar yangi vaqt/avtobus/haydovchi ma'lumotlari bilan in-place yangilanadi.
    """
    if not enabled():
        return {"sent": 0, "status": "disabled"}
    return send_poll_to_groups(
        route_id, target_date, graphs=graphs, route_name=route_name, force=False
    )


def handle_group_callback(chat_id: int | str, cq: dict, data: str) -> None:
    """Guruhdan kelgan callback query bosilganda ishlov beradi.

    Turlar:
      - gck:<pid>:<idx>  — Aniq grafikni tasdiqlash
      - gme:<pid>        — Tezkor o'z grafigini tasdiqlash
      - grf:<pid>        — Yangilash
      - ginfo:<pid>      — Ma'lumot xabari
    """
    cq_id = cq.get("id")
    from_user = cq.get("from") or {}
    user_id = from_user.get("id")
    first_name = str(from_user.get("first_name") or "")
    last_name = str(from_user.get("last_name") or "")
    full_user_name = f"{first_name} {last_name}".strip() or "Haydovchi"
    username = str(from_user.get("username") or "")

    parts = data.split(":")
    action = parts[0]
    pid = parts[1] if len(parts) > 1 else ""

    poll = get_poll(pid)
    if not poll:
        _answer_cq(cq_id, "⚠️ So'rovnoma ma'lumoti topilmadi yoki eskirgan.", alert=True)
        return

    items = poll.get("items", [])

    # ---- 1. INFO TUGMASI ----
    if action == "ginfo":
        _answer_cq(
            cq_id,
            "✅ Barcha haydovchilar ishga chiqishini tasdiqlagan!",
            alert=True,
        )
        return

    # ---- 2. REFRESH TUGMASI ----
    if action == "grf":
        try:
            poll = build_poll_data(
                poll.get("route_id", ""),
                poll.get("date", ""),
                route_name=poll.get("route_name", ""),
            )
        except Exception as ref_exc:
            log.warning("grf qayta yuklash xatosi: %s", ref_exc)
        update_poll_messages(poll)
        items = poll.get("items", [])
        confirmed_count = sum(1 for it in items if it.get("confirmed"))
        total = len(items)
        _answer_cq(
            cq_id,
            f"🔄 Yangilandi: {confirmed_count} / {total} ta tasdiqlandi.",
            alert=False,
        )
        return

    # Foydalanuvchi roli va bog'langan haydovchi profiling tekshirish
    from .roles import Role, resolve_role

    user_role = resolve_role(user_id) if user_id else Role.VIEWER
    is_privileged = user_role in (Role.ADMIN, Role.DISPATCHER)
    bound_did, bound_dname = get_bound_driver_info(user_id)
    bot_uname = get_bot_username()

    # ---- 3. TEZKOR O'Z GRAFIGINI TASDIQLASH (gme) ----
    if action == "gme":
        if is_privileged and not bound_did:
            _answer_cq(
                cq_id,
                "ℹ️ Siz dispetchersiz. Muayyan haydovchi grafigini tasdiqlash uchun uning tugmasini bosing.",
                alert=True,
            )
            return

        if not bound_did:
            _answer_cq(
                cq_id,
                f"⚠️ Sizning Telegram profilingiz haydovchiga bog'lanmagan!\n\n"
                f"O'z grafigingizni tasdiqlash uchun avval '🔗 Profilni botga bog'lash' tugmasini bosing "
                f"yoki @{bot_uname} botiga kirib, /start orqali profilingizni bog'lang.",
                alert=True,
            )
            return

        matched_item = None
        for it in items:
            did = str(it.get("driver_id") or "").strip()
            if did and did == bound_did:
                matched_item = it
                break

        if not matched_item:
            _answer_cq(
                cq_id,
                f"ℹ️ Sizning ismingiz ({bound_dname}) ushbu yo'nalishning ertangi grafigida topilmadi.\n\n"
                f"Agar boshqa yo'nalishda bo'lsangiz, o'sha guruhdagi so'rovnomani tekshiring.",
                alert=True,
            )
            return

        if matched_item.get("confirmed"):
            g = matched_item.get("graph")
            plate = matched_item.get("plate")
            _answer_cq(
                cq_id,
                f"ℹ️ Sizning grafigingiz (Grafik {g} | {plate}) allaqachon tasdiqlangan!",
                alert=True,
            )
            return

        # Tasdiqlash
        _confirm_item(
            poll,
            matched_item,
            user_id=user_id,
            user_name=full_user_name,
            username=username,
            is_privileged=is_privileged,
        )
        _answer_cq(
            cq_id,
            f"✅ Qabul qilindi!\n\n"
            f"Grafik: {matched_item.get('graph')} | {matched_item.get('plate')}\n"
            f"Haydovchi: {bound_dname}\n"
            f"Chiqish vaqti: {matched_item.get('start_time')}\n"
            f"Ertaga ishga chiqishingiz tasdiqlandi.",
            alert=True,
        )
        return

    # ---- 4. ANIQ GRAFIKNI TANLASH (gck) ----
    if action == "gck":
        if len(parts) < 3:
            _answer_cq(cq_id, "⚠️ Noto'g'ri so'rov.", alert=True)
            return
        try:
            target_idx = int(parts[2])
        except ValueError:
            _answer_cq(cq_id, "⚠️ Noto'g'ri grafik indeksi.", alert=True)
            return

        if target_idx < 0 or target_idx >= len(items):
            _answer_cq(cq_id, "⚠️ Grafik ro'yxatda topilmadi.", alert=True)
            return

        item = items[target_idx]

        # 1. Allaqachon tasdiqlanganmi?
        if item.get("confirmed"):
            cname = item.get("confirmed_by_name") or "boshqa haydovchi"
            ctime = item.get("confirmed_at") or ""
            _answer_cq(
                cq_id,
                f"⚠️ Bu grafik allaqachon tasdiqlangan!\n\n"
                f"Tasdiqladi: {cname} ({ctime})",
                alert=True,
            )
            return

        target_did = str(item.get("driver_id") or "").strip()
        target_name = compact_driver_name(item.get("driver_name") or "")

        # 2. Xavfsizlik va Shaxsni tekshirish (Identity check)
        # Guruhdagi tugma orqali faqat o'sha haydovchining o'zi tasdiqlashi mumkin.
        # Admin yoki dispetcher ham guruh tugmasini tasodifan bosib boshqa haydovchini
        # tasdiqlab qo'ymasligi uchun guruhda istisno qilinmaydi!
        if not bound_did:
            if is_privileged:
                _answer_cq(
                    cq_id,
                    f"ℹ️ Siz admin/dispetchersiz.\n\n"
                    f"Guruhdagi grafik tugmasini faqat haydovchining o'zi (o'z Telegram profilidan) "
                    f"tasdiqlashi mumkin. Boshqaruv uchun Dashboard'dan yoki /sorov_tasdiq buyrug'idan foydalaning.",
                    alert=True,
                )
            else:
                _answer_cq(
                    cq_id,
                    f"⚠️ Sizning Telegram profilingiz haydovchiga bog'lanmagan!\n\n"
                    f"Boshqa haydovchining nomidan tasdiqlay olmaysiz.\n"
                    f"Iltimos, avval pastdagi '🔗 Profilni botga bog'lash' tugmasini bosing "
                    f"yoki @{bot_uname} botiga kirib, /start orqali profilingizni bog'lang.",
                    alert=True,
                )
            return

        if not target_did or target_did != bound_did:
            _answer_cq(
                cq_id,
                f"⛔️ Siz boshqa haydovchining grafigini tasdiqlay olmaysiz!\n\n"
                f"Sizning profilingiz: {bound_dname} ga bog'langan.\n"
                f"Siz faqat o'zingizning grafigingizni tasdiqlashingiz mumkin.",
                alert=True,
            )
            return

        # 3. Bu foydalanuvchi allaqachon boshqa grafikni tasdiqlagan bo'lsa (bitta kishi 2 ta bosmasligi uchun)
        if user_id:
            for other in items:
                if (
                    other.get("idx") != target_idx
                    and other.get("confirmed")
                    and other.get("confirmed_by_id") == user_id
                ):
                    og = other.get("graph")
                    _answer_cq(
                        cq_id,
                        f"⚠️ Siz allaqachon Grafik {og} ni tasdiqlagansiz!\n"
                        f"Bitta haydovchi bir nechta grafikni tasdiqlay olmaydi.",
                        alert=True,
                    )
                    return

        # Tasdiqlash
        _confirm_item(
            poll,
            item,
            user_id=user_id,
            user_name=full_user_name,
            username=username,
            is_privileged=is_privileged,
        )
        _answer_cq(
            cq_id,
            f"✅ Qabul qilindi!\n\n"
            f"Grafik: {item.get('graph')} | {item.get('plate')}\n"
            f"Haydovchi: {target_name}\n"
            f"Chiqish vaqti: {item.get('start_time')}\n"
            f"Ertaga ishga chiqishingiz tasdiqlandi.",
            alert=True,
        )
        return

    _answer_cq(cq_id, "Qabul qilindi", alert=False)


def _confirm_item(
    poll: dict,
    item: dict,
    user_id: int | None,
    user_name: str,
    username: str = "",
    is_privileged: bool = False,
) -> None:
    """Bitta grafikni tasdiqlaydi, bazaga bog'laydi va xabarlarni yangilaydi."""
    now_str = datetime.now().strftime("%H:%M")
    item["confirmed"] = True
    item["confirmed_by_id"] = user_id
    item["confirmed_by_name"] = user_name
    item["confirmed_by_user"] = username
    item["confirmed_at"] = now_str
    if is_privileged:
        item["confirmed_by_role"] = "DISPATCHER"
    poll["updated_at"] = datetime.now().isoformat()

    # 1. Holatni saqlash
    save_poll(poll)

    # 2. Bildirishnomalar jadvaliga qayd
    try:
        st = get_storage()
        if st and st.enabled:
            st.record_notification(
                channel="tg_poll_ack",
                target=str(user_id or ""),
                message=f"{poll.get('route_name')} {poll.get('date')} Grafik {item.get('graph')} tasdiqladi",
                status="CONFIRMED",
            )
    except Exception:
        pass

    # 3. Foydalanuvchi faoliyatiga yozish
    if user_id:
        try:
            from ...core.bot_users import record_activity
            record_activity(
                user_id,
                "poll_confirmed",
                f"{poll.get('route_name', '')} {poll.get('date', '')} Grafik {item.get('graph')} | {item.get('plate')} ({item.get('start_time')}) tasdiqlandi",
            )
        except Exception:
            pass

    # 4. Guruhdagi xabarni yangilash
    update_poll_messages(poll)


def reset_confirmation(
    route_id_or_poll: str, target_date: str, graph_name: str
) -> bool:
    """Grafik tasdiqlanishini bekor qiladi (admin/dispetcher tomonidan)."""
    pid = (
        route_id_or_poll
        if "_" in route_id_or_poll
        else poll_id_for(route_id_or_poll, target_date)
    )
    poll = get_poll(pid)
    if not poll:
        return False

    found = False
    for it in poll.get("items", []):
        if str(it.get("graph") or "").strip() == str(graph_name).strip():
            it["confirmed"] = False
            it["confirmed_by_id"] = None
            it["confirmed_by_name"] = ""
            it["confirmed_by_user"] = ""
            it["confirmed_at"] = ""
            found = True
            break

    if found:
        save_poll(poll)
        update_poll_messages(poll)
    return found


def confirm_by_admin(
    route_id_or_poll: str,
    target_date: str,
    graph_name: str,
    admin_name: str = "Dispetcher",
) -> bool:
    """Grafikni admin/dispetcher tomonidan qo'lda tasdiqlaydi (buyruq yoki Dashboard orqali)."""
    pid = (
        route_id_or_poll
        if "_" in route_id_or_poll
        else poll_id_for(route_id_or_poll, target_date)
    )
    poll = get_poll(pid)
    if not poll:
        return False

    found_item = None
    for it in poll.get("items", []):
        if str(it.get("graph") or "").strip() == str(graph_name).strip():
            found_item = it
            break

    if not found_item or found_item.get("confirmed"):
        return False

    now_str = datetime.now().strftime("%H:%M")
    found_item["confirmed"] = True
    found_item["confirmed_by_id"] = None
    found_item["confirmed_by_name"] = f"{admin_name} (Dispetcher)"
    found_item["confirmed_by_user"] = ""
    found_item["confirmed_at"] = now_str
    found_item["confirmed_by_role"] = "DISPATCHER"
    poll["updated_at"] = datetime.now().isoformat()
    save_poll(poll)
    update_poll_messages(poll)
    return True


def swap_graphs(
    route_id_or_poll: str,
    target_date: str,
    graph1: str,
    graph2: str,
    admin_name: str = "Dispetcher",
) -> dict:
    """Ikki grafik o'rtasida haydovchi almashishini (swap) amalga oshiradi.

    graph1 va graph2 ning haydovchilari o'rin almashadi,
    har ikki grafik qayta tasdiqlash uchun ochiladi va guruh xabari yangilanadi.
    """
    pid = (
        route_id_or_poll
        if "_" in route_id_or_poll
        else poll_id_for(route_id_or_poll, target_date)
    )
    poll = get_poll(pid)
    if not poll:
        return {"ok": False, "error": "So'rovnoma topilmadi"}

    items = poll.get("items", [])
    item1 = None
    item2 = None
    g1_str = str(graph1).strip().upper()
    g2_str = str(graph2).strip().upper()

    for it in items:
        cur_g = str(it.get("graph") or "").strip().upper()
        if cur_g == g1_str or cur_g == f"P{g1_str}":
            item1 = it
        elif cur_g == g2_str or cur_g == f"P{g2_str}":
            item2 = it

    if not item1 or not item2:
        return {"ok": False, "error": f"Grafiklar topilmadi: {graph1} yoki {graph2}"}

    # Haydovchilarni o'zaro almashtiramiz
    d1_id, d1_name = item1.get("driver_id"), item1.get("driver_name")
    d2_id, d2_name = item2.get("driver_id"), item2.get("driver_name")

    item1["driver_id"] = d2_id
    item1["driver_name"] = d2_name
    item1["confirmed"] = False
    item1["confirmed_by_id"] = None
    item1["confirmed_by_name"] = ""
    item1["confirmed_by_user"] = ""
    item1["confirmed_at"] = ""
    item1["swapped_by"] = admin_name

    item2["driver_id"] = d1_id
    item2["driver_name"] = d1_name
    item2["confirmed"] = False
    item2["confirmed_by_id"] = None
    item2["confirmed_by_name"] = ""
    item2["confirmed_by_user"] = ""
    item2["confirmed_at"] = ""
    item2["swapped_by"] = admin_name

    poll["updated_at"] = datetime.now().isoformat()
    save_poll(poll)
    update_poll_messages(poll)

    return {
        "ok": True,
        "poll_id": pid,
        "graph1": item1.get("graph"),
        "driver1": compact_driver_name(d2_name),
        "graph2": item2.get("graph"),
        "driver2": compact_driver_name(d1_name),
    }


def replace_driver(
    route_id_or_poll: str,
    target_date: str,
    graph_name: str,
    new_driver_id: str,
    new_driver_name: str,
    admin_name: str = "Dispetcher",
) -> dict:
    """Grafikka boshqa haydovchini biriktiradi (almashtirish)."""
    pid = (
        route_id_or_poll
        if "_" in route_id_or_poll
        else poll_id_for(route_id_or_poll, target_date)
    )
    poll = get_poll(pid)
    if not poll:
        return {"ok": False, "error": "So'rovnoma topilmadi"}

    found = None
    g_str = str(graph_name).strip().upper()
    for it in poll.get("items", []):
        cur_g = str(it.get("graph") or "").strip().upper()
        if cur_g == g_str or cur_g == f"P{g_str}":
            found = it
            break

    if not found:
        return {"ok": False, "error": f"Grafik topilmadi: {graph_name}"}

    found["driver_id"] = new_driver_id
    found["driver_name"] = new_driver_name
    found["confirmed"] = False
    found["confirmed_by_id"] = None
    found["confirmed_by_name"] = ""
    found["confirmed_by_user"] = ""
    found["confirmed_at"] = ""
    found["swapped_by"] = admin_name

    poll["updated_at"] = datetime.now().isoformat()
    save_poll(poll)
    update_poll_messages(poll)

    return {
        "ok": True,
        "graph": found.get("graph"),
        "driver_name": compact_driver_name(new_driver_name),
    }


def get_poll_status_text(route_id: str, target_date: str) -> str:
    """/sorov_status buyrug'i uchun qisqacha hisobot."""
    pid = poll_id_for(route_id, target_date)
    poll = get_poll(pid)
    if not poll:
        return f"ℹ️ {target_date} sanasi uchun so'rovnoma hali yaratilmagan."

    items = poll.get("items", [])
    total = len(items)
    confirmed = [it for it in items if it.get("confirmed")]
    pending = [it for it in items if not it.get("confirmed")]

    route = poll.get("route_name") or route_id[:8]
    lines = [
        f"📊 <b>{escape(route)} ({poll.get('date')}) SO'ROVNOMA HOLATI:</b>",
        f"Jami grafiklar: {total} ta",
        f"✅ Tasdiqlaganlar: {len(confirmed)} ta",
        f"⏳ Kutilmoqda: {len(pending)} ta",
        "",
    ]

    if pending:
        lines.append("<b>Kutilayotgan haydovchilar:</b>")
        for it in pending:
            g = it.get("graph")
            name = compact_driver_name(it.get("driver_name") or "")
            plate = it.get("plate")
            lines.append(f"• Grafik {g}: {escape(name)} (<code>{escape(plate)}</code>)")
    else:
        lines.append("🎉 Barcha haydovchilar ishga chiqishini tasdiqlagan!")

    return "\n".join(lines)


def _answer_cq(cq_id: str, text: str, alert: bool = False) -> None:
    """Telegram answerCallbackQuery chaqirig'i."""
    if not cq_id:
        return
    try:
        telegram_call(
            "answerCallbackQuery",
            {"callback_query_id": cq_id, "text": text, "show_alert": alert},
        )
    except Exception:
        pass
