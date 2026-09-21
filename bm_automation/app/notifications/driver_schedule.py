"""Haydovchining ertangi smenasi uchun Telegram eslatmalari.

Kunlik jadval yaratilib DB ga sinxronlangandan keyin chaqiriladi. Har bir
haydovchi uchun bitta yig'ma xabar yuboriladi; xuddi shu jadval avval yuborilgan
bo'lsa, ``notifications`` jurnalidan topilib dublikat o'tkazib yuboriladi.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from html import escape

from ..db.storage import Storage
from ..utils.names import short_name
from .telegram import send_message


def _hhmm(value: str) -> str:
    """Vaqtdan HH:MM ni ajratadi (ISO timestamp yoki oddiy vaqt)."""
    value = str(value or "").strip()
    if not value:
        return ""
    if "T" in value:
        value = value.split("T", 1)[1]
    parts = value[:5].split(":")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return value


def _fmt_busy(start: str, end: str) -> str:
    """Boshlash va tugatish vaqti orasidagi ish intervalini chiqaradi."""
    s, e = _hhmm(start), _hhmm(end)
    if s and e:
        if s <= e:
            return f"{s} — {e}"
        return f"{s} (kechasi {e} gacha)"
    return s or e or "vaqt belgilanmagan"


def _message(name: str, schedule_date: str, rows: list[dict]) -> str:
    try:
        d = date.fromisoformat(schedule_date)
        day_name = {
            0: "Dushanba", 1: "Seshanba", 2: "Chorshanba",
            3: "Payshanba", 4: "Juma", 5: "Shanba", 6: "Yakshanba",
        }[d.weekday()]
        date_label = f"{d.day:02d}.{d.month:02d}.{d.year} ({day_name})"
    except Exception:  # noqa: BLE001
        date_label = schedule_date

    starts = [_hhmm(r.get("start_time")) for r in rows if _hhmm(r.get("start_time"))]
    ends = [_hhmm(r.get("end_time")) for r in rows if _hhmm(r.get("end_time"))]
    total_trips = sum(int(r.get("trip_count") or 0) for r in rows)

    lines = [
        "🚌 <b>ISHGA CHIQISH ESLATMASI</b>",
        "",
        f"Hurmatli <b>{escape(name)}</b>!",
        f"Sizning <b>{date_label}</b> kungi smenangiz tayyor:",
        "",
        "━━━━━━━━━━━━━━━━━━",
    ]

    # Umumiy ish vaqti hisoboti
    if starts:
        lines.append(f"🕒 Ishni boshlash: <b>{min(starts)}</b>")
    if ends:
        lines.append(f"🏁 Ishni tugatish: <b>{max(ends)}</b>")
    lines.append(f"🔁 Qatnovlar soni: <b>{total_trips}</b>")
    lines.append("━━━━━━━━━━━━━━━━━━")

    for i, row in enumerate(rows, 1):
        bus = escape(str(row.get("vehicle") or "avtobus belgilanmagan"))
        graph = escape(str(row.get("graph_name") or "grafik"))
        shift = escape(str(row.get("shift_name") or ""))
        busy = _fmt_busy(row.get("start_time"), row.get("end_time"))
        trips = int(row.get("trip_count") or 0)

        head = f"{i}. 🕒 {escape(busy)}"
        if shift:
            head += f" · {shift}"
        lines.append(head)
        lines.append(f"   • Yo'nalish grafigi: <b>{graph}</b>")
        lines.append(f"   • Avtobus: <b>{bus}</b>")
        if trips:
            lines.append(f"   • Qatnovlar: <b>{trips}</b>")

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "⚠️ Iltimos, ishga belgilangan vaqtda chiqing!",
        "Kecheksangiz, smena grafigi buzilishi mumkin.",
        "",
        "✅ Ushbu xabarni olganingizni tasdiqlash uchun, "
        "haydovchi bo'limida <b>/start</b> tugmasini bosing.",
    ])
    return "\n".join(lines)


def _delivery_summary(result: dict) -> str:
    """Yuborilgan bildirishnomalar hisobotini tayyorlaydi."""
    sent, skipped, failed = result.get("sent", 0), result.get("skipped", 0), result.get("failed", 0)
    if result.get("errors"):
        err_text = "\n".join(f"  ⚠️ {e}" for e in result["errors"][:5])
        return (f"📨 <b>Haydovchilarga bildirishnoma</b>\n"
                f"  ✅ Yuborildi: <b>{sent}</b>\n"
                f"  ⏭ O'tkazib yuborildi (avval yuborilgan): <b>{skipped}</b>\n"
                f"  ❌ Yuborilmadi: <b>{failed}</b>\n"
                f"{err_text}")
    return (f"📨 <b>Haydovchilarga bildirishnoma</b>\n"
            f"  ✅ Yuborildi: <b>{sent}</b>\n"
            f"  ⏭ O'tkazib yuborildi (avval yuborilgan): <b>{skipped}</b>\n"
            f"  ❌ Yuborilmadi: <b>{failed}</b>")


def send_driver_schedule_notifications(
    storage: Storage,
    route_id: str,
    schedule_date: str,
) -> dict:
    """Yoqilgan haydovchilarga smena eslatmasini yuboradi.

    Kunlik job qayta ishlasa, avval yuborilgan aynan bir xabar ``skipped``
    bo'ladi. Xato bitta haydovchida qolsa, boshqalariga yuborish davom etadi.
    """
    result = {"sent": 0, "skipped": 0, "failed": 0, "errors": [],
              "drivers": []}
    if not storage.enabled:
        result["errors"].append("DB rejimi o'chirilgan")
        return result

    ph = storage.db.ph
    rows = storage.query(
        "SELECT s.driver_id, s.graph_name, s.start_time, s.end_time, "
        "s.trip_count, s.shift_name, s.vehicle_id, "
        "d.full_name, p.notification_target, p.telegram_chat_id, "
        "v.plate_number "
        "FROM schedules s "
        "JOIN driver_profiles p ON p.driver_id = s.driver_id "
        "LEFT JOIN drivers d ON d.external_id = s.driver_id "
        "LEFT JOIN vehicles v ON v.external_id = s.vehicle_id "
        f"WHERE s.date = {ph} AND s.route_id = {ph} AND s.driver_id != '' "
        "AND p.notification_enabled = 1 AND p.blacklisted = 0 "
        "AND (p.notification_target != '' OR p.telegram_chat_id != '') "
        "ORDER BY s.driver_id, s.start_time",
        (schedule_date, route_id),
    )
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        row["vehicle"] = row.get("plate_number") or row.get("vehicle_id") or ""
        name = str(row.get("full_name") or row.get("driver_id") or "Haydovchi")
        name = short_name(name)
        # Telegram'ga bog'langan haydovchi — shaxsiy chat'iga; aks holda target.
        target = str(row.get("telegram_chat_id") or "").strip() \
            or str(row.get("notification_target") or "").strip()
        grouped[(str(row["driver_id"]), target, name)].append(row)

    for driver_id, target, name in grouped:
        message = _message(name, schedule_date, grouped[(driver_id, target, name)])
        entries = {
            "driver_id": driver_id,
            "name": name,
            "target": target,
            "message": message,
            "status": "",
        }
        sent_before = storage.query(
            "SELECT id FROM notifications "
            f"WHERE channel = {ph} AND target = {ph} AND message = {ph} AND status = 'SENT'",
            ("telegram", target, message), limit=1)
        if sent_before:
            result["skipped"] += 1
            entries["status"] = "skipped"
            result["drivers"].append(entries)
            continue
        try:
            send_message(message, chat_id=target)
            storage.record_notification(channel="telegram", target=target,
                                        message=message, status="SENT")
            result["sent"] += 1
            entries["status"] = "sent"
        except Exception as exc:  # noqa: BLE001 - qolgan haydovchilar yuboriladi
            storage.record_notification(channel="telegram", target=target,
                                        message=message, status="FAILED")
            result["failed"] += 1
            entries["status"] = "failed"
            entries["error"] = str(exc)
            result["errors"].append(f"{name}: {exc}")
        result["drivers"].append(entries)
    return result
