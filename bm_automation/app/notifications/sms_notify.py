"""Haydovchilarga kunlik SMS xabarnomalar (Android SMS Gateway — capcom6).

Lokal tarmoqdagi Android telefon+SIM karta orqali ishlaydigan ochiq kodli
shlyuz (``capcom6/android-sms-gateway``), uchinchi tomon pullik SMS API emas.
Ulanish ma'lumotlari kodga emas, ``.env`` dan o'qiladi:
    ANDROID_SMS_GATEWAY_LOGIN / ANDROID_SMS_GATEWAY_PASSWORD /
    ANDROID_SMS_GATEWAY_URL

Ish oqimi:
* ``send_driver_schedule_sms`` — ``run_daily`` oqimi, DB ``schedules`` asosida;
* ``send_sms_for_graphs`` — ``daily_grafik.py`` da BM duty grafiki asosida;
* ``retry_failed_sms`` / ``sms_log`` — dashboard uchun.

Har bir natija ``sms_log`` jadvaliga yoziladi. Bitta haydovchidagi xato
qolganlarga yuborishni to'xtatmaydi. SMS matni HTML'siz, qisqa; holati
gateway orqali ``Pending → Sent → Delivered`` kuzatiladi.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from ..config.settings import sms_gateway_configured, sms_gateway_settings
from ..db.storage import Storage, get_storage
from ..utils.logger import get_logger
from ..utils.names import short_name
from ..utils.tgformat import esc

log = get_logger(__name__)

# Muvaffaqiyatli yuborilgan deb hisoblanadigan statuslar (dublikatga yo'l
# qo'yilmaydi — shu kun+haydovchi uchun qayta yuborilmaydi). UPDATED —
# grafik o'zgarganda yuborilgan tuzatish xabari ham "yuborilgan" sanaladi.
_SENT_STATUSES = {"SENT", "DELIVERED", "PROCESSED", "UPDATED"}
_FINAL_FAILED = {"FAILED", "CANCELLED"}

_SMS_TIMEOUT_S = 40.0
_HTTP_TIMEOUT_S = 20.0

# PENDING holati shuncha soatdan ortiq qolsa "stale" hisoblanadi: holati
# gateway'dan qayta so'raladi, aniq chiqmasa xabar qayta yuboriladi.
_STALE_PENDING_H = 1.0
# Fon tekshiruvi orasidagi minimal interval (bot tsikli tez aylanadi —
# gateway'ni spamlamaslik uchun).
_STALE_POLL_INTERVAL_S = 3600.0
_LAST_STALE_POLL = 0.0

# Bitta haydovchiga kuniga ko'pi bilan shuncha SMS yuboriladi. Qoida:
# kunga 1 ta smena xabari; grafik o'zgarsa 1 ta tuzatish (UPDATED) —
# jami 2. Bundan ko'p yuborish dublikat hisoblanadi va bloklanadi
# (SMS balans tejash uchun).
_MAX_DAY_SMS = 2
# Kunlik limitni hisoblashda "yuborilgan" deb hisoblanadigan statuslar —
# gateway'ga murojaat qilingan (balans sarflagan) har qanday urinish.
_DAY_SEND_STATUSES = (_SENT_STATUSES | {"PENDING", "PROCESSED", "UNKNOWN",
                                        "ANNULLED", "REMOVED", "OFF",
                                        "FAILED", "CANCELLED"})


def _state_value(state) -> str:
    """Gateway ProcessState enum'ini toza status qatoriga keltiradi.

    ``str(ProcessState.SENT)`` ba'zan ``PROCESSSTATE.SENT`` ko'rinishida
    chiqadi — oxirgi nuqtadan keyingi qism olinadi: ``SENT``.
    """
    s = str(state or "").strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.upper()


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
            return f"{s}-{e}"
        return f"{s} (kechasi {e} gacha)"
    return s or e or "vaqt belgilanmagan"


def _date_label(schedule_date: str) -> str:
    try:
        d = date.fromisoformat(schedule_date)
        day_name = {
            0: "Dushanba", 1: "Seshanba", 2: "Chorshanba",
            3: "Payshanba", 4: "Juma", 5: "Shanba", 6: "Yakshanba",
        }[d.weekday()]
        return f"{d.day:02d}.{d.month:02d}.{d.year} ({day_name})"
    except Exception:  # noqa: BLE001 - ma'lumot chetki bo'lsa xato qaytmaydi
        return schedule_date


def _normalize_phone(raw: str) -> str:
    """Telefon raqamini xalqaro formatga keltiradi.

    Qabul qilinadigan shakllar: ``+998901234567``, ``998901234567``,
    ``901234567``, ``8 90 123 45 67``. Maskalangan (``+9****48``) yoki
    noto'g'ri raqam bo'sh qator qaytaradi.
    """
    raw = str(raw or "").strip()
    if not raw or "*" in raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    if len(digits) == 13 and digits.startswith("998"):
        # "+998..." — 13 ta raqam deb massiv yozilgan holatlar ham bor.
        return "+" + digits
    if len(digits) == 9 and digits[0] in "903394959798983387":
        return "+998" + digits
    if len(digits) == 10 and digits.startswith("8") and digits[1] in "903394959798983387":
        return "+998" + digits[1:]
    if len(digits) == 9 and digits.startswith("8"):
        return "+998" + digits[1:]
    return ""


def _detail_lines(rows: list[dict], route_name: str = "") -> list[str]:
    """Smena tafsilotlari qatorlari (sana/yo'nalish + har reys)."""
    starts = [_hhmm(r.get("start_time")) for r in rows if _hhmm(r.get("start_time"))]
    ends = [_hhmm(r.get("end_time")) for r in rows if _hhmm(r.get("end_time"))]
    total_trips = sum(int(r.get("trip_count") or 0) for r in rows)

    lines: list[str] = []
    if starts:
        lines.append(f"Boshlash: {min(starts)}")
    if ends:
        lines.append(f"Tugatish: {max(ends)}")
    if total_trips:
        lines.append(f"Qatnovlar: {total_trips}")
    if route_name:
        lines.append(f"Yo'nalish: {route_name}")

    lines.append("-----")
    for i, row in enumerate(rows, 1):
        bus = str(row.get("vehicle") or "avtobus belgilanmagan")
        graph = str(row.get("graph_name") or "grafik")
        shift = str(row.get("shift_name") or "")
        busy = _fmt_busy(row.get("start_time"), row.get("end_time"))
        trips = int(row.get("trip_count") or 0)
        line = f"{i}) {busy}"
        if shift:
            line += f" {shift}"
        lines.append(line)
        lines.append(f"   {graph} / {bus}")
        if trips:
            lines.append(f"   qatnov: {trips}")
    return lines


def _dispatcher_phone(storage: Storage, route_id: str) -> str:
    """Yo'nalishga biriktirilgan dispetcher telefoni (SMS footeriga)."""
    try:
        if storage.enabled and hasattr(storage, "dispatcher_phone"):
            return str(storage.dispatcher_phone(route_id) or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _contact_line(d_phone: str = "") -> str:
    if d_phone:
        return f"Savollar bo'lsa dispetcherga murojaat qiling. Tel: {d_phone}"
    return "Savollar bo'lsa dispetcherga murojaat qiling."


def _message(name: str, schedule_date: str, rows: list[dict],
             route_name: str = "", d_phone: str = "") -> str:
    """SMS uchun qisqa oddiy matn (HTML'siz, ~450 belgi)."""
    date_label = _date_label(schedule_date)

    lines = [
        "BM AVTOMATSION: ISHGA CHIQISH",
        "",
        f"Assalomu alaykum, {name}!",
        f"Sizning {date_label} kungi smenangiz tayyor:",
    ]
    lines.extend(_detail_lines(rows, route_name=route_name))
    lines.extend([
        "-----",
        "Iltimos, ishga o'z vaqtida chiqing!",
        _contact_line(d_phone),
    ])
    text = "\n".join(lines)
    return text[:500]


def _update_message(name: str, schedule_date: str, rows: list[dict],
                    route_name: str = "", d_phone: str = "") -> str:
    """Grafik o'zgargan haydovchiga yuboriladigan tuzatish matni."""
    date_label = _date_label(schedule_date)

    lines = [
        "BM AVTOMATSION: SMENANGIZ O'ZGARTIRILDI",
        "",
        f"Assalomu alaykum, {name}!",
        f"{date_label} kuni smenangiz o'zgartirildi. Yangi grafik:",
    ]
    lines.extend(_detail_lines(rows, route_name=route_name))
    lines.extend([
        "-----",
        "Iltimos, yangi grafik bo'yicha ishga chiqing!",
        _contact_line(d_phone),
    ])
    text = "\n".join(lines)
    return text[:500]


def _gateway_client():
    """Gateway API clientini tuzadi (lokal tarmoq, vaqt limiti bilan)."""
    cfg = sms_gateway_settings()
    url = (cfg.get("url") or "").strip().rstrip("/")
    login = (cfg.get("login") or "").strip() or None
    password = cfg.get("password") or ""
    if not password or not url:
        return None
    try:
        import requests
        from android_sms_gateway import APIClient
        from android_sms_gateway.http import RequestsHttpClient
    except Exception:  # noqa: BLE001 - kutubxona yo'q bo'lsa aniq xato beriladi
        raise RuntimeError("android_sms_gateway kutubxonasi o'rnatilmagan")

    session = requests.Session()

    def _timed_request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", _HTTP_TIMEOUT_S)
        return requests.Session.request(self, method, url, **kwargs)

    import types
    session.request = types.MethodType(_timed_request, session)
    return APIClient(login, password, base_url=url,
                     http=RequestsHttpClient(session=session))


def _call_with_timeout(fn, timeout: float = _SMS_TIMEOUT_S):
    """Funktsiyani vaqt chegarasi bilan boshqa thread'da bajaradi."""
    out: dict = {}

    def _run():
        try:
            out["result"] = fn()
        except Exception as exc:  # noqa: BLE001 - hech qanday xato qolmasligi
            out["error"] = exc

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, TimeoutError(f"so'rov {int(timeout)} s ichida tugamadi")
    if "error" in out:
        return None, out["error"]
    return out.get("result"), None


def _recipient_summary(rec) -> dict:
    """Gateway RecipientState obyektidan qisqa dict qaytaradi."""
    return {
        "phone_number": str(getattr(rec, "phone_number", "") or ""),
        "state": _state_value(getattr(rec, "state", "")),
        "error": str(getattr(rec, "error", "") or ""),
    }


def send_sms(phone: str, text: str) -> dict:
    """Bitta telefon raqamiga SMS yuboradi va holatini qaytaradi.

    Holat ``sms_log``ga yozilmaydi — dashboardda ko'rinadigan yozuv uchun
    ``send_and_log`` (yoki `_send_one`) ishlatiladi.
    """
    result = {"ok": False, "phone": _normalize_phone(phone), "status": "FAILED",
              "message_id": "", "error": "", "states": []}
    if not result["phone"]:
        result["error"] = "telefon raqami noto'g'ri"
        result["status"] = "INVALID"
        return result
    if not sms_gateway_configured():
        result["error"] = "SMS shlyuzi .env da sozlanmagan"
        return result
    try:
        from android_sms_gateway import Message, TextMessage
        client = _gateway_client()
        if client is None:
            result["error"] = "SMS shlyuzi .env da sozlanmagan"
            return result
        msg = Message(phone_numbers=[result["phone"]],
                      text_message=TextMessage(text=str(text)),
                      with_delivery_report=True)
        state, exc = _call_with_timeout(
            lambda: client.send(msg, skip_phone_validation=True))
        if exc is not None:
            result["error"] = str(exc)
            return result
    except Exception as exc:  # noqa: BLE001 - status xabarni uzadi
        result["error"] = str(exc)
        return result

    result["ok"] = True
    result["message_id"] = str(getattr(state, "id", "") or "")
    recipients = getattr(state, "recipients", None) or []
    recs = [_recipient_summary(r) for r in recipients]
    result["states"] = recs
    top_state = _state_value(getattr(state, "state", ""))
    status = top_state or "PENDING"
    if recs:
        rs = recs[0].get("state") or ""
        if rs:
            status = rs
        result["error"] = recs[0].get("error") or ""
    result["status"] = status
    return result


def send_and_log(phone: str, text: str, name: str = "", driver_id: str = "",
                 route_id: str = "", route_name: str = "",
                 schedule_date: str = "", storage: Storage | None = None,
                 update_existing: bool = False) -> dict:
    """SMS yuboradi va natijani ``sms_log``ga yozadi (dashboard uchun).

    ``update_existing=True`` bo'lsa, ayni telefon+matn uchun oxirgi ``FAILED``/
    ``INVALID`` yozuv yangilanadi (dublikat osilarishning oldini oladi).
    """
    st = storage or get_storage()
    payload = send_sms(str(phone or ""), str(text or ""))
    status = payload.get("status") or "FAILED"
    message_id = payload.get("message_id") or ""
    error = payload.get("error") or ""
    send_at = now_iso()

    if update_existing and status not in ("INVALID",) and message_id:
        ph = st.db.ph
        not_taken = ", ".join([ph] * len(_SENT_STATUSES))
        row = st.query(
            f"SELECT id FROM sms_log WHERE phone = {ph} AND message = {ph} "
            f"AND status NOT IN ({not_taken}) ORDER BY id DESC",
            (str(phone or ""), str(text or ""), *_SENT_STATUSES), limit=1)
        if row:
            st.update_sms_status(int(row[0]["id"]), status=status,
                                 message_id=message_id, error=error)
            return {**payload, "logged": "updated", "log_id": int(row[0]["id"])}

    st.record_sms(driver_id=driver_id, name=name, phone=str(payload.get("phone") or phone or ""),
                  route_id=route_id, route_name=route_name,
                  schedule_date=schedule_date, message=str(text or ""),
                  status=status, message_id=message_id, error=error,
                  send_at=send_at)
    if status not in _SENT_STATUSES and status not in _FINAL_FAILED and message_id:
        polled = _poll_sms_update(st, message_id)
        if polled:
            status = polled
            payload = {**payload, "status": polled}
    return {**payload, "logged": "inserted"}


def now_iso() -> str:
    """Lokal vaqtni ISO formatida qaytaradi (send_at uchun)."""
    return datetime.now().isoformat()


def fetch_sms_status(message_id: str) -> dict:
    """Yuborilgan SMS holatini gateway'dan qayta so'raydi (polling)."""
    result = {"ok": False, "message_id": message_id or "",
              "state": "UNKNOWN", "recipients": []}
    if not sms_gateway_configured() or not message_id:
        return result
    try:
        from android_sms_gateway import Message, MessagesQueryFilter, QueryPagination
        client = _gateway_client()
        if client is None:
            return result
        states, exc = _call_with_timeout(
            lambda: client.get_messages(
                query=MessagesQueryFilter(),
                pagination=QueryPagination(limit=50),
                sort="-created_at"))
        if exc is not None:
            result["error"] = str(exc)
            return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        return result

    for st in states or []:
        if str(getattr(st, "id", "") or "") == str(message_id):
            result["ok"] = True
            result["state"] = _state_value(getattr(st, "state", ""))
            recs = [_recipient_summary(r)
                    for r in (getattr(st, "recipients", None) or [])]
            result["recipients"] = recs
            if recs and recs[0].get("state"):
                result["state"] = recs[0]["state"]
            return result
    result["error"] = "topilmadi"
    return result


# ---------------------------------------------------------------- DB helpers


def _load_phone_map(storage: Storage) -> dict[str, str]:
    """driver_id -> telefon raqami (normalizatsiyalangan)."""
    out: dict[str, str] = {}
    if not storage.enabled:
        return out
    rows = storage.query(
        f"SELECT driver_id, phone FROM driver_profiles "
        f"WHERE phone != '' AND blacklisted = 0")
    for r in rows:
        did = str(r.get("driver_id") or "")
        phone = _normalize_phone(r.get("phone"))
        if did and phone:
            out.setdefault(did, phone)
    return out


def _load_name_map(storage: Storage) -> dict[str, str]:
    """external_id -> full_name (grafikdagi haydovchi nomini solishtirish)."""
    out: dict[str, str] = {}
    if not storage.enabled:
        return out
    rows = storage.query("SELECT external_id, full_name FROM drivers")
    for r in rows:
        eid = str(r.get("external_id") or "")
        if eid:
            out[eid] = str(r.get("full_name") or "")
    return out


def _graphs_to_rows(storage: Storage, graphs: list[dict],
                    schedule_date: str) -> list[dict]:
    """Duty grafiklarini SMS qatorlariga aylantiradi (sekond haydovchi bilan)."""
    if not graphs:
        return []
    phone_map = _load_phone_map(storage)
    name_map = _load_name_map(storage)
    # reverse: full_name -> external_id (faqat bir xil nom bo'lsa)
    name_to_id: dict[str, str] = {}
    for eid, full in name_map.items():
        if full:
            name_to_id.setdefault(str(full).strip().lower(), eid)

    rows = []
    for g in graphs or []:
        vehicle = (str(g.get("plateNum") or "").strip()
                   or str(g.get("vehicleId") or "").strip()
                   or str(g.get("vehicleModel") or "").strip())
        base = dict(
            driver_id=str(g.get("driverId") or ""),
            name=str(g.get("driverName") or "").strip(),
            graph_name=str(g.get("graphName") or "").strip(),
            shift_name=str(g.get("shiftName") or "").strip(),
            start_time=str(g.get("startTime") or "").strip(),
            end_time=str(g.get("endTime") or "").strip(),
            trip_count=int(g.get("tripCount") or 0),
            vehicle=vehicle,
        )
        main = dict(base)
        phone = ""
        did = main["driver_id"]
        if did in phone_map:
            phone = phone_map[did]
        elif main["name"] and main["name"].lower() in name_to_id:
            phone = phone_map.get(name_to_id[main["name"].lower()], "")
        main["phone"] = phone
        main["second"] = "0"
        rows.append(main)

        if g.get("hasSecond") and g.get("secondDriverName"):
            sec = dict(base)
            sec["name"] = str(g.get("secondDriverName") or "").strip()
            sec["driver_id"] = ""
            sec["phone"] = ""
            if sec["name"].lower() in name_to_id:
                sec["driver_id"] = name_to_id[sec["name"].lower()]
                if sec["driver_id"] in phone_map:
                    sec["phone"] = phone_map[sec["driver_id"]]
            sec["second"] = "1"
            rows.append(sec)
    return rows


def _load_schedule_rows(storage: Storage, schedule_date: str,
                        route_id: str) -> list[dict]:
    """DB `schedules` dan SMS uchun haydovchi qatorlarini yig'adi."""
    if not storage.enabled:
        return []
    ph = storage.db.ph
    sql = (
        "SELECT s.driver_id, s.graph_name, s.start_time, s.end_time, "
        "s.trip_count, s.shift_name, s.vehicle_id, "
        "d.full_name, p.phone, v.plate_number "
        "FROM schedules s "
        "LEFT JOIN driver_profiles p ON p.driver_id = s.driver_id "
        "LEFT JOIN drivers d ON d.external_id = s.driver_id "
        "LEFT JOIN vehicles v ON v.external_id = s.vehicle_id "
        f"WHERE s.date = {ph} AND s.driver_id != '' "
        "AND p.blacklisted = 0"
    )
    params: tuple = (schedule_date,)
    if route_id:
        sql += f" AND s.route_id = {ph}"
        params += (route_id,)
    sql += " ORDER BY s.driver_id, s.start_time"
    rows = storage.query(sql, params)
    out = []
    for row in rows:
        out.append({
            "driver_id": str(row.get("driver_id") or ""),
            "name": short_name(str(row.get("full_name") or row.get("driver_id") or "Haydovchi")),
            "graph_name": str(row.get("graph_name") or ""),
            "shift_name": str(row.get("shift_name") or ""),
            "start_time": str(row.get("start_time") or ""),
            "end_time": str(row.get("end_time") or ""),
            "trip_count": int(row.get("trip_count") or 0),
            "vehicle": (str(row.get("plate_number") or "")
                        or str(row.get("vehicle_id") or "")),
            "phone": _normalize_phone(row.get("phone")),
        })
    return out


def _group_rows(rows: list[dict]) -> dict[tuple[str, str, str], list[dict]]:
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        key = (str(row["driver_id"]), str(row["phone"]), str(row["name"]))
        grouped[key].append(row)
    return grouped


def _sent_before(storage: Storage, schedule_date: str, route_id: str,
                 driver_id: str, phone: str) -> bool:
    """Shu kun+yo'nalish+haydovchi uchun SMS avval yuborilganmi.

    Muvaffaqiyatli (SENT/DELIVERED/PROCESSED) yozuv har doim bloklaydi.
    Yakunlanmagan yozuv (PENDING/PROCESSED/UNKNOWN) mavjud bo'lsa ham har
    doim bloklaydi — qaysi sanada yuborilganidan qat'i nazar. Chunki bitta
    jismoniy yo'nalish uchun SMS bir nechta oqimdan keladi (daily_grafik,
    daily, grafik kuzatuvi); PENDING qolgan birinchi urinishdan KEYIN
    keyingi oqim qayta-qayta yuborib, haydovchiga dublikat SMS ("ikki
    yo'nalish"dek ko'rinadigan) yetib borardi. Status keyinroq yetib
    borishi (poll) orqali SENT/DELIVERED'ga yangilanadi. Shlyuz javob
    bermagan hollarda qayta urinish faqat FAILED bo'lsa mumkin.
    """
    if not storage.enabled:
        return False
    ph = storage.db.ph
    rows = storage.query(
        f"SELECT status FROM sms_log "
        f"WHERE schedule_date = {ph} AND route_id = {ph} "
        f"AND driver_id = {ph} AND phone = {ph} "
        f"ORDER BY id DESC LIMIT 50",
        (schedule_date, route_id, driver_id, phone))
    for r in rows:
        stt = str(r.get("status") or "").upper()
        if stt in _SENT_STATUSES or stt in {"PENDING", "PROCESSED", "UNKNOWN"}:
            return True
    return False


def _day_send_count(storage: Storage, schedule_date: str,
                    driver_id: str, phone: str) -> int:
    """Shu kun+haydovchi uchun gateway'ga qilingan SMS urinishlar soni.

    Kunlik limit (``_MAX_DAY_SMS``) uchun: yuborilgan (balans sarflagan)
    har qanday urinish hisoblanadi — SENT/DELIVERED, PENDING, FAILED va
    "chiqarildi" (ANNULLED/etc.) hammasi. Shunday qilib bir haydovchiga
    kuniga ko'pi bilan 2 marta yuboriladi (1 smena + 1 tuzatish), qolgan
    loop'lar dublikat sifatida bloklanadi.
    """
    if not storage.enabled:
        return 0
    ph = storage.db.ph
    in_ph = ", ".join([ph] * len(_DAY_SEND_STATUSES))
    rows = storage.query(
        f"SELECT COUNT(*) AS n FROM sms_log "
        f"WHERE schedule_date = {ph} AND driver_id = {ph} AND phone = {ph} "
        f"AND status IN ({in_ph})",
        (schedule_date, driver_id, phone, *_DAY_SEND_STATUSES))
    return int((rows[0] or {}).get("n") or 0) if rows else 0


# Chiqarilgan haydovchi bildirishnomasi sifatida yoziladigan statuslar.
# `_sent_before` ularni muvaffaqiyatli yuborilgan deb hisoblamaydi, shuning
# uchun ajratib tekshiriladi — takroriy "chiqarildi" SMS yuqmaydi.
_OFF_STATUSES = {"ANNULLED", "REMOVED", "OFF"}
# "Chiqarildi" xabari yuborilganini ko'rsatadigan "kutuvchi" statuslar:
# gateway tasdiqlashi kechiksa SMS PENDING bo'lib qoladi — uni ham ANNULLED
# deb hisoblasak, keyingi tekshiruvlar takroriy "chiqarildi" yubormaydi.
_OFF_PENDING_STATUSES = {"PENDING", "PROCESSED", "UNKNOWN"}


def _annulled_before(storage: Storage, schedule_date: str, route_id: str,
                     driver_id: str, phone: str) -> bool:
    """Shu kun+yo'nalish+haydovchi uchun "chiqarildi" xabari avval yuborilganmi."""
    if not storage.enabled:
        return False
    ph = storage.db.ph
    in_ph = ", ".join([ph] * len(_OFF_STATUSES))
    rows = storage.query(
        f"SELECT id FROM sms_log WHERE schedule_date = {ph} "
        f"AND route_id = {ph} AND driver_id = {ph} AND phone = {ph} "
        f"AND status IN ({in_ph}) LIMIT 1",
        (schedule_date, route_id, driver_id, phone, *_OFF_STATUSES))
    if rows:
        return True
    # Tasdiq kelmagan (PENDING) "chiqarildi" xabari ham bor bo'lsa —
    # qayta yuborilmaydi (aks holda har 15 daqiqada dublikat ketardi).
    in_pnd = ", ".join([ph] * len(_OFF_PENDING_STATUSES))
    rows = storage.query(
        f"SELECT id FROM sms_log WHERE schedule_date = {ph} "
        f"AND route_id = {ph} AND driver_id = {ph} AND phone = {ph} "
        f"AND status IN ({in_pnd}) "
        f"AND message LIKE {ph} LIMIT 1",
        (schedule_date, route_id, driver_id, phone, *_OFF_PENDING_STATUSES,
         "%chiqarildi%"))
    return bool(rows)


def _removal_message(name: str, schedule_date: str, d_phone: str = "") -> str:
    """Grafikdan chiqarilgan haydovchiga yuboriladigan xabar."""
    date_label = _date_label(schedule_date)
    name_part = f"{name}, " if name else ""
    return (
        f"Assalomu alaykum, {name_part}BM AVTOMATSION tizimi.\n"
        f"Siz {date_label} kungi grafikdan chiqarildingiz — o'rningizga "
        f"boshqa haydovchi qo'yilgan. Ertaga ishga chiqishingiz shart emas.\n"
        + _contact_line(d_phone)
    )[:500]


def _notify_removed_tg(storage: Storage, schedule_date: str, route_id: str,
                       name: str, driver_id: str) -> bool:
    """Chiqarilgan haydovchiga Telegram orqali ham ogohlantirish yuboradi.

    Matn SMS'dagi ``_removal_message`` bilan bir xil (HTML-eskap qilingan).
    ``driver_profiles`` da ``telegram_chat_id`` va ``notification_enabled``
    bo'lgan haydovchiga yuboriladi; ``notifications`` jadvalida shu
    chat+matn allaqachon SENT bo'lsa takrorlanmaydi.
    """
    if not storage.enabled:
        return False
    try:
        prof = storage.find("driver_profiles", driver_id=driver_id) or {}
    except Exception:  # noqa: BLE001 - profil topilmasa Telegram o'tkazib yuboriladi
        return False
    chat = str(prof.get("telegram_chat_id") or "").strip()
    if not chat or not prof.get("notification_enabled"):
        return False
    ph = storage.db.ph
    message = _removal_message(name, schedule_date,
                               _dispatcher_phone(storage, route_id))
    text = f"⚠️ <b>Grafikdan chiqarildi</b>\n{esc(message)}"
    rows = storage.query(
        "SELECT id FROM notifications "
        f"WHERE channel = {ph} AND target = {ph} AND message = {ph} "
        "AND status = 'SENT' LIMIT 1",
        ("telegram", chat, text))
    if rows:
        return False
    try:
        from .telegram import send_message  # import paytida xato bo'lmasligi uchun
        send_message(text, chat_id=chat)
        storage.record_notification(channel="telegram", target=chat,
                                    message=text, status="SENT",
                                    sent_at=datetime.now(
                                        timezone.utc).isoformat())
        return True
    except Exception as exc:  # noqa: BLE001 - SMS asosiy kanal, Telegram ixtiyoriy
        log.warning("chiqarilgan haydovchi Telegram xabari yuborilmadi"
                    " (%s): %s", name, exc)
        return False


def _notify_removed(storage: Storage, schedule_date: str, route_id: str,
                    route_name: str, current_keys: set, result: dict) -> None:
    """Grafikdan tushib qolgan haydovchilarni aniqlab, ularga SMS yuboradi.

    ``current_keys`` — hozirgi grafikdagi (driver_id, phone, name) majmui.
    Avval ushbu kun+yo'nalish uchun muvaffaqiyatli yuborilgan (sms_log) ammo
    endi grafikda yo'q haydovchilar "chiqarildi" xabari bilan ogohlantiriladi.
    """
    if not storage.enabled or not route_id:
        return
    ph = storage.db.ph
    in_ph = ", ".join([ph] * len(_SENT_STATUSES))
    rows = storage.query(
        f"SELECT driver_id, name, phone FROM sms_log "
        f"WHERE schedule_date = {ph} AND route_id = {ph} "
        f"AND driver_id != {ph} AND phone != {ph} "
        f"AND status IN ({in_ph}) "
        f"ORDER BY id DESC",
        (schedule_date, route_id, "", "", *_SENT_STATUSES))
    # Stale PENDING yozuvlar ham hisobga olinadi: holati tasdiqlanmagan
    # (PENDING) bo'lsa ham ushbu haydovchi avval grafikda bo'lgan — endi
    # grafikdan chiqarilgan bo'lsa "chiqarildi" xabari yetib borishi kerak.
    pend_rows = storage.query(
        f"SELECT driver_id, name, phone FROM sms_log "
        f"WHERE schedule_date = {ph} AND route_id = {ph} "
        f"AND driver_id != {ph} AND phone != {ph} "
        f"AND status IN ({ph}, {ph}) "
        f"ORDER BY id DESC",
        (schedule_date, route_id, "", "", "PENDING", "PROCESSED"))
    prev: dict[str, dict] = {}
    for r in rows + pend_rows:
        d = str(r.get("driver_id") or "")
        if d and d not in prev:
            prev[d] = {"name": str(r.get("name") or d),
                       "phone": _normalize_phone(r.get("phone"))}
    if not prev:
        return

    current_dids = {str(k[0]) for k in current_keys}
    for d_id, old in prev.items():
        if d_id in current_dids:
            continue
        name = old["name"]
        # Telegram: SMS chiqarilgan xabari bilan bir xil matn, lekin
        # kunlik SMS limiti bloklagan taqdirda ham haydovchi xabardor bo'ladi.
        _notify_removed_tg(storage, schedule_date, route_id, name, d_id)
        phone = old["phone"]
        if not phone:
            continue
        if _annulled_before(storage, schedule_date, route_id, d_id, phone):
            continue
        # Kunlik limit — haydovchi allaqachon kuniga 2 ta SMS olgan
        # bo'lsa "chiqarildi" xabari ham bloklanadi.
        if _day_send_count(storage, schedule_date, d_id, phone) \
                >= _MAX_DAY_SMS:
            continue
        entry = {"driver_id": d_id, "name": name, "phone": phone,
                 "route_id": route_id, "route_name": route_name,
                 "status": "ANNULLED", "message": "",
                 "error": "", "message_id": "", "removed": True}
        try:
            message = _removal_message(name, schedule_date,
                                       _dispatcher_phone(storage, route_id))
            payload = send_sms(phone, message)
            status = payload.get("status") or "FAILED"
            entry["message"] = message
            entry["message_id"] = payload.get("message_id") or ""
            entry["error"] = payload.get("error") or ""
            if status in _SENT_STATUSES:
                # "Chiqarildi" xabari muvaffaqiyatli — ANNULLED sifatida
                # yoziladi, shunda qayta yuborilmaydi va `_sent_before`
                # uni muvaffaqiyatli ish-xabar deb hisoblamaydi.
                entry["status"] = "ANNULLED"
                storage.record_sms(driver_id=d_id, name=name, phone=phone,
                                   route_id=route_id, route_name=route_name,
                                   schedule_date=schedule_date, message=message,
                                   status="ANNULLED",
                                   message_id=entry["message_id"],
                                   error="")
                result["removed"] += 1
                result["drivers"].append(entry)
                log.info("Grafikdan chiqarilgan haydovchi ogohlantirildi: %s",
                         name)
            else:
                entry["status"] = status
                storage.record_sms(driver_id=d_id, name=name, phone=phone,
                                   route_id=route_id, route_name=route_name,
                                   schedule_date=schedule_date, message=message,
                                   status=status,
                                   message_id=entry["message_id"],
                                   error=entry["error"])
                result["failed"] += 1
                result["errors"].append(
                    f"{name}: chiqarilganligi haqida SMS yuborilmadi"
                    f"{(entry['error'] and ': ' + entry['error']) or ''}")
                result["drivers"].append(entry)
        except Exception as exc:  # noqa: BLE001 - qolganlar yuboriladi
            result["failed"] += 1
            result["errors"].append(f"{name}: {exc}")
            log.warning("Chiqarilgan haydovchi SMS xato (%s): %s", name, exc)


# ------------------------------------------------------------- asosiy yuborish


def _poll_sms_update(storage: Storage, message_id: str,
                     attempts: int = 3, delay: float = 4.0) -> str:
    """Yuborilgan SMS holatini bir necha marta so'rab, sms_log'ni yangilaydi.

    Gateway yetkazish holatini biroz kechikish bilan yangilaydi (PENDING →
    SENT → DELIVERED). Oxirgi kuzatilgan status qaytariladi; yangilanmasa
    bo'sh qator.
    """
    import time  # noqa: PLC0415 - faqat polling holatida import
    if not message_id:
        return ""
    known = _SENT_STATUSES | _FINAL_FAILED | {"PENDING", "UNKNOWN"}
    last = ""
    for _ in range(max(attempts, 1)):
        try:
            st = fetch_sms_status(message_id)
            new_status = st.get("state") or ""
            if new_status in known:
                last = new_status
            # UNKNOWN/final bo'lmagan muqobil — yana kutamiz faqat agar actual
            # holat hali yakunlanmagan bo'lsa.
            if new_status not in _FINAL_FAILED and new_status not in _SENT_STATUSES \
                    and new_status and new_status != "UNKNOWN":
                time.sleep(delay)
                continue
        except Exception as exc:  # noqa: BLE001 - holat sinovi bloklamaydi
            log.debug("SMS holat tekshiruvi xatolik: %s", exc)
            break
        break
    # Faqat ma'noli holatlar jurnalga yoziladi (UNKNOWN "topilmadi" degani —
    # mavjud statusni buzmaymiz).
    if last and last != "UNKNOWN":
        try:
            ph = storage.db.ph
            upd = storage.query(
                f"SELECT id FROM sms_log WHERE message_id = {ph} "
                f"ORDER BY id DESC",
                (message_id,), limit=1)
            if upd:
                storage.update_sms_status(int(upd[0]["id"]), status=last,
                                          message_id=message_id, error="")
        except Exception as exc:  # noqa: BLE001
            log.debug("SMS holat yozuvini yangilash xatolik: %s", exc)
    return last


def _sent_success_elsewhere(storage: Storage, schedule_date: str,
                            route_id: str, driver_id: str, phone: str,
                            exclude_row_id: int) -> bool:
    """Shu kun+yo'nalish+haydovchi uchun BOSHQA yozuv allaqachon muvaffaqiyatlimi.

    ``exclude_row_id`` — qayta urinilayotgan yozuvning o'zi (FAILED/PENDING)
    hisobga olinmaydi; aks holda u o'zini "allaqachon yuborilgan" deb
    hisoblab, qayta yuborishni bloklardi.
    """
    if not storage.enabled:
        return False
    ph = storage.db.ph
    in_ph = ", ".join([ph] * len(_SENT_STATUSES))
    rows = storage.query(
        f"SELECT id FROM sms_log "
        f"WHERE schedule_date = {ph} AND route_id = {ph} "
        f"AND driver_id = {ph} AND phone = {ph} "
        f"AND status IN ({in_ph}) AND id != {ph} LIMIT 1",
        (schedule_date, route_id, driver_id, phone, *_SENT_STATUSES,
         int(exclude_row_id)))
    return bool(rows)


def _msg_key(text: str) -> str:
    """Xabarni solishtirish uchun sarlavha (header) qismini tashlab beradi.

    Oddiy xabar ("ISHGA CHIQISH") va tuzatish ("SMENANGIZ
    O'ZGARTIRILDI") matnlari bir xil tafsilot qismiga ega — grafik
    o'zgarishini faqat shu qismdan solishtirish to'g'ri bo'ladi.
    """
    t = str(text or "")
    if "-----" in t:
        t = t.split("-----", 1)[1]
        if "-----" in t:
            t = t.rsplit("-----", 1)[0]
    return t.strip().lower()


def resolve_route_name(storage: Storage, route_id: str, fallback: str = "") -> str:
    """Yo'nalishning yagona (kanonik) nomi — barcha oqimlar uchun.

    Tartib: profiles.json (routeName) → routes jadvali → route_daily →
    fallback. Profil birinchi: u har doim mavjud va boshqa manbalarga
    qaraganda barqaror ("10-yo'nalish", "B-80"...). Bu funksiyadan
    foydalanish sms_log'da bir yo'nalish ikki xil nom ("10-YO'NALISH" /
    "10-yo'nalish") bilan yozilishining oldini oladi.
    """
    rid = str(route_id or "").strip()
    try:
        from ..core.profiles import all_profiles  # noqa: PLC0415
        for p in all_profiles():
            if str(p.get("routeVariantId") or "").strip() == rid:
                n = str(p.get("routeName") or "").strip()
                if n:
                    return n
    except Exception:  # noqa: BLE001
        pass
    if storage.enabled:
        try:
            import json as _json  # noqa: PLC0415
            r = storage.find("routes", external_id=rid) or {}
            n = str(r.get("name") or "").strip()
            if n:
                return n
            rows = storage.query(
                f"SELECT data FROM route_daily WHERE route_id = {storage.db.ph} "
                "AND data LIKE %s ORDER BY date DESC LIMIT 1",
                (rid, '%"routeName"%')) if storage.db.ph == "%s" else []
            if rows:
                d = _json.loads(str((rows[0] or {}).get("data") or "{}"))
                n = str(d.get("routeName") or "").strip()
                if n:
                    return n
        except Exception:  # noqa: BLE001
            pass
    return str(fallback or "").strip()


def retry_stale_pending(
    storage: Storage | None = None,
    stale_hours: float = _STALE_PENDING_H,
    limit: int = 100,
) -> dict:
    """1 soatdan ortiq PENDING qolgan SMS'larni avtomatik qayta urinadi.

    Avval gateway'dan xabar holati qayta so'raladi (dublikat yuborilmasligi
    uchun): aniq status chiqsa — jurnal shunchaki yangilanadi. Aniq chiqmasa
    (UNKNOWN/yo'q): shu haydovchiga boshqa urinishdan allaqachon muvaffaqiyatli
    SMS ketgan bo'lsa eski yozuv SUPERSEDED deb belgilanadi (qayta urinilmaydi,
    haydovchiga dublikat ketmaydi); aks holda xabar QAYTA YUBORILADI va yangi
    natija jurnalga yoziladi (eski PENDING yozuv tarix uchun joyida qoladi).
    Bot fon tsiklida soatiga bir marta chaqiriladi.
    """
    global _LAST_STALE_POLL
    now_mono = datetime.now().timestamp()
    st = storage or get_storage()
    result = {"polled": 0, "resolved": 0, "resent": 0, "failed": 0,
              "skipped": 0, "errors": []}
    if not st.enabled or not sms_gateway_configured():
        return result
    if now_mono - _LAST_STALE_POLL < _STALE_POLL_INTERVAL_S:
        return result
    _LAST_STALE_POLL = now_mono

    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=stale_hours)).isoformat()
    ph = st.db.ph
    rows = st.query(
        f"SELECT * FROM sms_log WHERE status = {ph} "
        f"AND created_at < {ph} AND phone != {ph} "
        f"AND schedule_date >= {ph} "
        f"ORDER BY id LIMIT {max(int(limit), 1)}",
        ("PENDING", cutoff, "",
         (date.today() - timedelta(days=2)).isoformat()),
    )
    if not rows:
        return result
    log.info("Stale PENDING SMS tekshiruvi: %s yozuv", len(rows))
    for row in rows:
        row_id = int(row.get("id") or 0)
        mid = str(row.get("message_id") or "")
        phone = _normalize_phone(row.get("phone"))
        message = str(row.get("message") or "")
        result["polled"] += 1
        # 1) Gateway'dan haqiqiy holatni so'raymiz
        final = ""
        if mid:
            try:
                stt = fetch_sms_status(mid)
                state = str((stt or {}).get("state") or "").upper()
            except Exception:  # noqa: BLE001
                state = ""
            if state in _SENT_STATUSES:
                st.update_sms_status(row_id, status=state,
                                     message_id=mid, error="")
                result["resolved"] += 1
                continue
            if state in _FINAL_FAILED:
                st.update_sms_status(row_id, status=state,
                                     message_id=mid,
                                     error=str((stt or {}).get("error") or ""))
                result["failed"] += 1
                continue
        # 2) Aniq bo'lmasa — shu haydovchiga BOSHQA urinishdan muvaffaqiyatli
        #    SMS ketgan-yetmaganini tekshiramiz (dublikatdan himoya).
        if _sent_success_elsewhere(st, str(row.get("schedule_date") or ""),
                                   str(row.get("route_id") or ""),
                                   str(row.get("driver_id") or ""), phone,
                                   exclude_row_id=row_id):
            # Haydovchi xabarni allaqachon olgan — eski PENDING yozuvni
            # "SUPERSEDED" deb belgilaymiz (qayta urinilmaydi).
            st.update_sms_status(row_id, status="SUPERSEDED",
                                 message_id=mid, error="")
            result["skipped"] += 1
            continue
        if not phone or not message:
            result["skipped"] += 1
            continue
        # Kunlik limit: haydovchi bugun allaqachon 2 tadan ortiq SMS olgan
        # bo'lmasligi kerak (qayta urinish ham dublikat hisoblanadi).
        if _day_send_count(st, str(row.get("schedule_date") or ""),
                           str(row.get("driver_id") or ""), phone) \
                >= _MAX_DAY_SMS:
            st.update_sms_status(row_id, status="SUPERSEDED",
                                 message_id=mid, error="")
            result["skipped"] += 1
            continue
        payload = send_sms(phone, message)
        status = payload.get("status") or "FAILED"
        st.record_sms(
            driver_id=str(row.get("driver_id") or ""),
            name=str(row.get("name") or ""),
            phone=phone,
            route_id=str(row.get("route_id") or ""),
            route_name=str(row.get("route_name") or ""),
            schedule_date=str(row.get("schedule_date") or ""),
            message=message, status=status,
            message_id=payload.get("message_id") or "",
            error=payload.get("error") or "")
        if status in _SENT_STATUSES:
            result["resent"] += 1
        else:
            result["failed"] += 1
    log.info("Stale PENDING natija: %s", {k: v for k, v in result.items()
                                          if k != "errors"})
    return result


def _previous_message(storage: Storage, schedule_date: str, route_id: str,
                      driver_id: str, phone: str,
                      include_pending: bool = False) -> str:
    """Shu kun+haydovchi uchun oxirgi yuborilgan SMS matni.

    Grafik o'zgarishini aniqlash uchun: hozirgi matn avval yuborilganidan
    farq qilsa — haydovchiga tuzatish (UPDATED) xabari yuboriladi.

    ``include_pending=True`` bo'lsa PENDING/PROCESSED yozuvlar ham
    hisobga olinadi: tasdiqlash kelmagan (stale PENDING) yozuvdan keyin
    grafik o'zgargan bo'lsa, haydovchi baribir yangi ma'lumotni olishi
    kerak ("chiqarildi" holatida esa umuman yozuv bo'lmasligi mumkin).
    """
    if not storage.enabled:
        return ""
    ph = storage.db.ph
    statuses = _SENT_STATUSES | ({"PENDING", "PROCESSED", "UNKNOWN"}
                                 if include_pending else set())
    in_ph = ", ".join([ph] * len(statuses))
    rows = storage.query(
        f"SELECT message FROM sms_log "
        f"WHERE schedule_date = {ph} AND route_id = {ph} "
        f"AND driver_id = {ph} AND phone = {ph} "
        f"AND status IN ({in_ph}) ORDER BY id DESC LIMIT 1",
        (schedule_date, route_id, driver_id, phone, *statuses))
    if not rows:
        return ""
    return str((rows[0] or {}).get("message") or "")


def _send_one(storage: Storage, key, rows: list[dict], schedule_date: str,
              route_id: str, route_name: str, result: dict) -> dict:
    """Bitta haydovchiga SMS yuboradi va sms_log'ga yozadi.

    Agar shu kun+yo'nalish uchun avval boshqa matn yuborilgan bo'lsa
    (grafik o'zgargan) — oddiy xabar o'rniga tuzatish (UPDATED) xabarini
    yuboradi; grafik o'zgarmagan bo'lsa dublikat yuborilmaydi (SKIPPED).
    """
    driver_id, phone, name = key
    d_phone = _dispatcher_phone(storage, route_id)
    message = _message(name, schedule_date, rows, route_name=route_name,
                       d_phone=d_phone)

    entry = {
        "driver_id": driver_id, "name": name, "phone": phone,
        "route_id": route_id, "status": "INVALID", "message": message,
        "error": "", "message_id": "",
    }

    # Stale PENDING: gateway'dan tasdiqlash kelmagan yozuv. Shunday yozuv
    # bo'lsa ham matn o'zgargan bo'lsa KORREKSIYA yuboriladi (dublikat emas —
    # yangi ma'lumot); matn bir xil bo'lsa o'tkazib yuboriladi.
    correction = False
    if _sent_before(storage, schedule_date, route_id, driver_id, phone):
        prev = _previous_message(storage, schedule_date, route_id,
                                 driver_id, phone, include_pending=True)
        if prev and _msg_key(prev) != _msg_key(message):
            correction = True
        else:
            result["skipped"] += 1
            entry["status"] = "SKIPPED"
            result["drivers"].append(entry)
            return entry

    # Kunlik limit: bir haydovchiga kuniga ko'pi bilan _MAX_DAY_SMS ta SMS
    # (1 smena + 1 tuzatish). Grafik shunchaki almashsaham yoki gateway
    # PENDING qolsa ham qayta-yuborish bloklanadi.
    if _day_send_count(storage, schedule_date, driver_id, phone) \
            >= _MAX_DAY_SMS:
        result["skipped"] += 1
        entry["status"] = "SKIPPED"
        entry["error"] = "kunlik limit (2) to'ldi"
        result["drivers"].append(entry)
        return entry

    if not phone:
        result["invalid"] += 1
        entry["error"] = "telefon raqami yo'q"
        storage.record_sms(driver_id=driver_id, name=name, phone=phone,
                           route_id=route_id, route_name=route_name,
                           schedule_date=schedule_date, message=message,
                           status="INVALID",
                           error=entry["error"])
        result["drivers"].append(entry)
        return entry

    text = _update_message(name, schedule_date, rows, route_name=route_name,
                           d_phone=d_phone) \
        if correction else message
    payload = send_sms(phone, text)
    status = payload.get("status") or "FAILED"
    if correction and status in _SENT_STATUSES:
        status = "UPDATED"
    entry.update({
        "status": status,
        "message_id": payload.get("message_id") or "",
        "error": payload.get("error") or "",
        "message": text,
    })
    if status == "UPDATED":
        result["updated"] += 1
    elif status in _SENT_STATUSES:
        result["sent"] += 1
    elif status in _FINAL_FAILED:
        result["failed"] += 1
        result["errors"].append(f"{name}: {entry['error'] or 'yuborilmadi'}")
    else:
        result["pending"] += 1

    storage.record_sms(
        driver_id=driver_id, name=name, phone=phone,
        route_id=route_id, route_name=route_name,
        schedule_date=schedule_date, message=text, status=status,
        message_id=entry["message_id"], error=entry["error"])
    result["drivers"].append(entry)

    # Pending/Processed qolsa, holatni qayta so'rab yozuvni yangilaymiz
    # (SMS haydovchiga yetganini kuzatish).
    if status not in _SENT_STATUSES and status not in _FINAL_FAILED \
            and entry["message_id"]:
        new_status = _poll_sms_update(storage, entry["message_id"])
        if new_status and new_status != status:
            entry["status"] = new_status
            if new_status == "UPDATED":
                result["updated"] += 1
                result["pending"] = max(result["pending"] - 1, 0)
            elif new_status in _SENT_STATUSES:
                result["sent"] += 1
                result["pending"] = max(result["pending"] - 1, 0)
    return entry


def _route_label(storage: Storage, route_id: str) -> str:
    """Yo'nalish uchun yagona kanonik nom (sms_log'ga bir xil yozilishi uchun)."""
    return resolve_route_name(storage, route_id)


def send_driver_schedule_sms(
    storage: Storage,
    schedule_date: str,
    route_id: str | None = None,
) -> dict:
    """Kunlik jadvaldan haydovchilarga SMS yuboradi (``run_daily`` oqimi)."""
    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    if not storage.enabled:
        result["errors"].append("DB rejimi o'chirilgan")
        return result
    if not sms_gateway_configured():
        result["errors"].append("SMS shlyuzi .env da sozlanmagan "
                                "(ANDROID_SMS_GATEWAY_*)")
        return result
    if route_id and not _route_enabled(storage, route_id):
        result["errors"].append(f"Yo'nalish SMS uchun o'chirilgan: {route_id}")
        return result

    route_name = _route_label(storage, route_id)
    rows = _load_schedule_rows(storage, schedule_date, route_id or "")
    current_keys = set()
    if not rows:
        _notify_removed(storage, schedule_date, route_id or "", route_name,
                        current_keys, result)
        return result

    for key, items in _group_rows(rows).items():
        current_keys.add(key)
        try:
            _send_one(storage, key, items, schedule_date,
                      route_id or "", route_name, result)
        except Exception as exc:  # noqa: BLE001 - qolganlar yuboriladi
            result["failed"] += 1
            result["errors"].append(f"{key[2]}: {exc}")
            log.warning("SMS yuborishda xato (%s): %s", key[2], exc)

    _notify_removed(storage, schedule_date, route_id or "", route_name,
                    current_keys, result)
    log.info("Haydovchi SMS: sent=%s skipped=%s failed=%s pending=%s "
             "invalid=%s updated=%s removed=%s", result["sent"],
             result["skipped"], result["failed"], result["pending"],
             result["invalid"], result["updated"], result["removed"])
    return result


def send_sms_for_graphs(
    storage: Storage,
    graphs: list[dict],
    route_id: str,
    schedule_date: str,
    route_name: str = "",
) -> dict:
    """BM duty grafigi bo'yicha haydovchilarga SMS yuboradi (daily_grafik)."""
    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    if not storage.enabled:
        result["errors"].append("DB rejimi o'chirilgan")
        return result
    if not sms_gateway_configured():
        result["errors"].append("SMS shlyuzi .env da sozlanmagan "
                                "(ANDROID_SMS_GATEWAY_*)")
        return result
    if not _route_enabled(storage, route_id):
        result["errors"].append(f"Yo'nalish SMS uchun o'chirilgan: {route_id}")
        return result
    rows = [r for r in _graphs_to_rows(storage, graphs, schedule_date)]
    current_keys = set()
    if not rows:
        _notify_removed(storage, schedule_date, route_id, route_name,
                        current_keys, result)
        return result

    for key, items in _group_rows(rows).items():
        current_keys.add(key)
        try:
            _send_one(storage, key, items, schedule_date,
                      route_id, route_name, result)
        except Exception as exc:  # noqa: BLE001
            result["failed"] += 1
            result["errors"].append(f"{key[2]}: {exc}")
            log.warning("SMS yuborishda xato (%s): %s", key[2], exc)

    _notify_removed(storage, schedule_date, route_id, route_name,
                    current_keys, result)
    log.info("Grafik SMS: sent=%s skipped=%s failed=%s pending=%s "
             "invalid=%s updated=%s removed=%s",
             result["sent"], result["skipped"], result["failed"],
             result["pending"], result["invalid"], result["updated"],
             result["removed"])
    return result


def sms_log(storage: Storage | None = None, limit: int = 200,
            status: str = "") -> dict:
    """SMS jurnalini dashboard uchun qaytaradi (oxirgi yozuvlar birinchi)."""
    st = storage or get_storage()
    rows = st.sms_log(limit=limit, status=status.strip().upper())
    return {
        "rows": rows,
        "count": len(rows),
        "counts": st.sms_log_counts(),
        "configured": sms_gateway_configured(),
    }


def _route_enabled(storage: Storage, route_id: str) -> bool:
    """Yo'nalish uchun SMS faolmi (dashboard flag; bo'sa faol)."""
    try:
        return storage.sms_route_enabled(route_id or "")
    except Exception:  # noqa: BLE001 - flag o'qilmaganda xavfsiz tarafda yubormaymiz
        log.warning("sms_route_enabled xatosi (%s): flagni chetlab o'tilmoqda",
                    route_id)
        return True


def sms_route_list(storage: Storage | None = None, routes: list | None = None,
                   route_names: dict | None = None) -> list:
    """Dashboard uchun yo'nalishlar ro'yxati + faollik holati.

    `routes` berilsa shu ro'yxat asosida (nomi uchun) chiqariladi; aks holda
    faqat flag yozilgan yo'nalishlar qaytariladi. Berilmagan yo'nalishlar
    standart faol (enabled=True) hisoblanadi.
    """
    st = storage or get_storage()
    flags = {str(r.get("route_id") or ""): int(r.get("enabled") or 0)
             for r in st.sms_route_list()}
    if routes is not None:
        result = []
        for rid in routes:
            rid = str(rid or "").strip()
            if not rid:
                continue
            name = (route_names or {}).get(rid, "")
            result.append({"route_id": rid, "route_name": name or rid,
                           "enabled": bool(flags.get(rid, 1))})
        return result
    return [{"route_id": rid, "route_name": rid,
             "enabled": bool(enabled)} for rid, enabled in flags.items()]


def sms_route_set(storage: Storage | None = None, route_id: str = "",
                  enabled: bool | None = None) -> dict:
    """Bitta yo'nalish yoki hammasini yoqish/o'chirish."""
    st = storage or get_storage()
    if route_id and enabled is not None:
        ok = st.sms_route_set(route_id, bool(enabled))
        return {"ok": ok, "route_id": route_id, "enabled": bool(enabled)}
    if enabled is not None:
        n = st.sms_route_set_all(bool(enabled))
        return {"ok": True, "count": n, "enabled": bool(enabled)}
    return {"ok": False, "error": "route_id yoki enabled kerak"}


def retry_failed_sms(storage: Storage | None = None, limit: int = 50) -> dict:
    """``FAILED``/``INVALID`` va stale ``PENDING`` SMS'larni qayta yuboradi.

    Dashboard tugmasi. PENDING/PROCESSED — gateway tasdiqlashi kelmayan
    (qotib qolgan) yozuvlar: qayta urinish ularni hal qiladi (SENT/FAILED).
    """
    st = storage or get_storage()
    result = {"sent": 0, "failed": 0, "skipped": 0, "errors": [],
              "retried": []}
    if not st.enabled:
        result["errors"].append("DB rejimi o'chirilgan")
        return result
    if not sms_gateway_configured():
        result["errors"].append("SMS shlyuzi .env da sozlanmagan")
        return result

    ph = st.db.ph
    # Diqqat: o'zi `_sent_before`ga murojaat qilmaydi — PENDING yozuvning
    # o'zi blok qo'yib, hech narsani qayta yubormaslikka olib kelardi.
    # FAILED/INVALID + stale PENDING/PROCESSED to'g'ridan-to'g'ri qayta uriniladi.
    rows = st.query(
        f"SELECT * FROM sms_log WHERE status IN ({ph}, {ph}, {ph}, {ph}, {ph}) "
        f"ORDER BY id DESC LIMIT {max(int(limit or 50), 1)}",
        ("FAILED", "INVALID", "CANCELLED", "PENDING", "PROCESSED"),
    )
    for row in reversed(rows):
        row_id = int(row.get("id") or 0)
        phone = _normalize_phone(row.get("phone"))
        if not phone:
            result["skipped"] += 1
            continue
        # DIQQAT: bu yerda `_sent_before` CHAQIRILMAYDI — qotib qolgan
        # PENDING yozuvning o'zi blok qo'yib, retry hech narsani qayta
        # yubormasligiga olib kelardi (dashboard tugmasi samarasiz bo'lardi).
        # Faqat BOSHQA yozuv allaqachon muvaffaqiyatli bo'lsa dublikat yo'q.
        if _sent_success_elsewhere(st, str(row.get("schedule_date") or ""),
                                   str(row.get("route_id") or ""),
                                   str(row.get("driver_id") or ""), phone,
                                   exclude_row_id=row_id):
            result["skipped"] += 1
            continue
        # Kunlik limit: haydovchi bugun allaqachon 2 ta SMS olgan bo'lsa
        # qo'lda (dashboard) qayta yuborish ham bloklanadi.
        if _day_send_count(st, str(row.get("schedule_date") or ""),
                           str(row.get("driver_id") or ""), phone) \
                >= _MAX_DAY_SMS:
            result["skipped"] += 1
            continue
        payload = send_sms(phone, str(row.get("message") or ""))
        status = payload.get("status") or "FAILED"
        st.update_sms_status(row_id, status=status,
                             message_id=payload.get("message_id") or "",
                             error=payload.get("error") or "")
        rec = {
            "id": row_id, "name": str(row.get("name") or ""),
            "phone": phone, "status": status,
            "error": payload.get("error") or "",
        }
        result["retried"].append(rec)
        if status in _SENT_STATUSES:
            result["sent"] += 1
        else:
            result["failed"] += 1
            if payload.get("error"):
                result["errors"].append(str(payload.get("error")))
    return result