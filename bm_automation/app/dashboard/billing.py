"""Korxonalarning oylik to'lovi (obuna billing).

Har korxonaning oylik narxi qo'lda belgilanadi (`companies.billing_amount`).
To'lovlar `company_payments` jadvaliga yoziladi: naqd, karta yoki hisob
raqami (shartnoma asosida). Davr to'liq to'langanda obuna muddati
(`subscription_expire_date`) shu davr oxirigacha uzaytiriladi.

Holatlar (`state`):
    active  — obuna amal qilmoqda
    due     — muddat tugashiga REMIND_DAYS kun yoki kam qoldi
    grace   — muddat tugagan, GRACE_DAYS ichida (bloklanmagan)
    blocked — grace ham o'tgan (yoki status bo'yicha to'xtatilgan)
    none    — obuna muddati belgilanmagan (fail-open)
"""

from __future__ import annotations

import calendar
import math
import re
from datetime import date, datetime, timedelta

from ..core.tenant import GRACE_DAYS, _INACTIVE_STATUSES

REMIND_DAYS = 5

METHODS: dict[str, str] = {
    "cash": "Naqd pul",
    "card": "Karta",
    "bank": "Hisob raqami",
}

_PERIOD_RE = re.compile(r"[0-9]{4}-[0-9]{2}")
_EXPIRY_NOTICE = (
    "Obuna muddati avtomatik qisqartirilmaydi; kerak bo'lsa korxona "
    "sozlamalarida qo'lda o'zgartiring."
)

STATE_LABELS: dict[str, str] = {
    "active": "Faol",
    "due": "To'lov vaqti",
    "grace": "Grace davri",
    "blocked": "Bloklangan",
    "none": "Muddat yo'q",
    "deleted": "O'chirilgan",
}


def _parse_date(raw) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _norm_period(raw) -> str:
    period = str(raw or "").strip()
    if not _PERIOD_RE.fullmatch(period):
        raise ValueError("Davr YYYY-MM ko'rinishida bo'lishi kerak")
    try:
        date.fromisoformat(period + "-01")
    except ValueError as exc:
        raise ValueError("Davr noto'g'ri (YYYY-MM)") from exc
    return period


def _norm_date(raw, *, allow_empty: bool = False) -> str:
    if allow_empty and raw == "":
        return ""
    error = "Sana YYYY-MM-DD ko'rinishida bo'lishi kerak"
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
        raise ValueError(error)
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError(error) from exc


def _positive_id(raw, label: str) -> int:
    error = f"{label} ID noto'g'ri"
    if (isinstance(raw, bool) or not isinstance(raw, (int, str))
            or not re.fullmatch(r"[0-9]+", str(raw))):
        raise ValueError(error)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(error) from exc
    if not 0 < value <= 9223372036854775807:
        raise ValueError(error)
    return value


def _payment_values(period, amount, method, contract_no, receipt_no, paid_on, note) -> dict:
    period = _norm_period(period)
    try:
        if isinstance(amount, bool):
            raise ValueError
        amount = float(amount)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Summa raqam bo'lishi kerak") from exc
    if not math.isfinite(amount):
        raise ValueError("Summa chekli raqam bo'lishi kerak")
    amount = round(amount, 2)
    if amount <= 0:
        raise ValueError("Summa 0 dan katta bo'lishi kerak")
    if not isinstance(method, str) or method.strip().lower() not in METHODS:
        raise ValueError("To'lov usuli: naqd, karta yoki hisob raqami")
    method = method.strip().lower()
    if not all(isinstance(value, str) for value in (contract_no, receipt_no, note)):
        raise ValueError("Shartnoma, chek raqami va izoh matn bo'lishi kerak")
    contract_no = contract_no.strip()
    if method == "bank" and not contract_no:
        raise ValueError("Hisob raqami uchun shartnoma raqami kerak")
    paid_on = _norm_date(date.today().isoformat() if paid_on == "" else paid_on)
    return {
        "period": period, "amount": amount, "method": method,
        "contract_no": contract_no, "receipt_no": receipt_no.strip(),
        "paid_on": paid_on, "note": note.strip(),
    }


def period_end(period: str) -> date:
    p = _norm_period(period)
    year, month = int(p[:4]), int(p[5:7])
    return date(year, month, calendar.monthrange(year, month)[1])


def current_period(today: date | None = None) -> str:
    return (today or date.today()).strftime("%Y-%m")


def _storage(storage=None):
    if storage is not None:
        return storage
    from ..db.storage import get_storage
    return get_storage()


def _totals(st, company_id: int = 0) -> dict[int, dict[str, float]]:
    """company_id -> {period: to'langan summa}."""
    sql = "SELECT company_id, period, amount FROM company_payments"
    params: tuple = ()
    if company_id:
        sql += f" WHERE company_id = {st.db.ph}"
        params = (company_id,)
    out: dict[int, dict[str, float]] = {}
    for r in st.query(sql, params, limit=100000):
        cid = int(r.get("company_id") or 0)
        period = str(r.get("period") or "")
        try:
            amount = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        out.setdefault(cid, {})
        out[cid][period] = out[cid].get(period, 0.0) + amount
    return out


def _company_state(row: dict, paid_periods: dict[str, float],
                   today: date | None = None) -> dict:
    """Bitta korxona uchun billing holati."""
    today = today or date.today()
    period = current_period(today)
    expire = _parse_date(row.get("subscription_expire_date"))
    try:
        price = float(row.get("billing_amount") or 0)
    except (TypeError, ValueError):
        price = 0.0
    paid = round(float(paid_periods.get(period, 0.0)), 2)
    debt = round(price - paid, 2) if price > 0 and paid < price else 0.0
    status = str(row.get("status") or "").strip().lower()

    days_left: int | None = None
    if status == "deleted":
        state = "deleted"
    elif status in _INACTIVE_STATUSES:
        state = "blocked"
    elif not expire:
        state = "none"
    else:
        days_left = (expire - today).days
        if days_left > REMIND_DAYS:
            state = "active"
        elif days_left >= 0:
            state = "due"
        elif days_left >= -GRACE_DAYS:
            state = "grace"
        else:
            state = "blocked"

    paid_through = ""
    for p, total in paid_periods.items():
        if price > 0 and total + 0.009 >= price:
            end = period_end(p).isoformat()
            if end > paid_through:
                paid_through = end

    return {
        "state": state,
        "state_label": STATE_LABELS.get(state, state),
        "days_left": days_left,
        "period": period,
        "price": round(price, 2),
        "paid": paid,
        "debt": debt,
        "paid_through": paid_through,
    }


def list_billing(storage=None, today: date | None = None) -> dict:
    """Barcha korxonalar bo'yicha to'lov holati + oxirgi to'lovlar."""
    st = _storage(storage)
    today = today or date.today()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas", "companies": [],
                "payments": [], "summary": {}}
    rows = st.query(
        "SELECT id, name, slug, status, subscription_plan, "
        "subscription_expire_date, billing_amount, billing_day, "
        "contact_telegram, contact_phone FROM companies ORDER BY name",
        limit=1000)
    totals = _totals(st)
    companies: list[dict] = []
    summary = {"active": 0, "due": 0, "grace": 0, "blocked": 0, "none": 0,
               "monthly_total": 0.0, "debt_total": 0.0}
    for r in rows:
        cid = int(r.get("id") or 0)
        info = _company_state(dict(r), totals.get(cid, {}), today=today)
        item = {**dict(r), **info}
        companies.append(item)
        if info["state"] == "deleted":
            continue
        summary[info["state"]] = summary.get(info["state"], 0) + 1
        summary["monthly_total"] += info["price"]
        summary["debt_total"] += info["debt"]
    for key in ("monthly_total", "debt_total"):
        summary[key] = round(summary[key], 2)
    payments = [dict(p) for p in st.query(
        "SELECT id, company_id, company, period, amount, method, "
        "contract_no, receipt_no, paid_on, note, created_by, created_at "
        "FROM company_payments ORDER BY paid_on DESC, id DESC", limit=200)]
    return {"ok": True, "companies": companies, "payments": payments,
            "summary": summary}


def record_payment(company_id: int, period: str, amount, storage=None,
                   method: str = "cash", contract_no: str = "",
                   receipt_no: str = "", paid_on: str = "",
                   note: str = "", created_by: str = "") -> dict:
    """To'lovni yozadi va (davr to'liq to'langan bo'lsa) obunani uzaytiradi."""
    st = _storage(storage)
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    try:
        cid = _positive_id(company_id, "Korxona")
        values = _payment_values(period, amount, method, contract_no,
                                 receipt_no, paid_on, note)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    rows = st.query(f"SELECT id, name, status, billing_amount, "
                    f"subscription_expire_date FROM companies "
                    f"WHERE id = {st.db.ph}", (cid,), limit=1)
    if not rows:
        return {"ok": False, "error": "Korxona topilmadi"}
    company = dict(rows[0])
    inserted = st.insert("company_payments", {
        **values,
        "company_id": cid,
        "company": str(company.get("name") or ""),
        "created_by": str(created_by or "").strip(),
    })
    if not inserted:
        return {"ok": False, "error": "To'lov yozilmadi (DB xatosi)"}
    return _payment_result(st, company, values, "yozildi")


def update_payment(payment_id: int, period: str, amount, storage=None,
                   method: str = "cash", contract_no: str = "",
                   receipt_no: str = "", paid_on: str = "", note: str = "") -> dict:
    """To'lovni tuzatadi; korxona va yaratilish ma'lumotlari o'zgarmaydi."""
    st = _storage(storage)
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    try:
        pid = _positive_id(payment_id, "To'lov")
        values = _payment_values(period, amount, method, contract_no,
                                 receipt_no, paid_on, note)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    rows = st.query(f"SELECT company_id FROM company_payments WHERE id = {st.db.ph}",
                    (pid,), limit=1)
    if not rows:
        return {"ok": False, "error": "To'lov topilmadi"}
    cid = int(rows[0]["company_id"])
    rows = st.query(f"SELECT id, name, status, billing_amount, "
                    f"subscription_expire_date FROM companies WHERE id = {st.db.ph}",
                    (cid,), limit=1)
    if not rows:
        return {"ok": False, "error": "Korxona topilmadi"}
    company = dict(rows[0])
    from ..db.models import now_utc
    set_clause = ", ".join(f"{key} = {st.db.ph}" for key in values)
    if not st._exec(
            f"UPDATE company_payments SET {set_clause}, updated_at = {st.db.ph} "
            f"WHERE id = {st.db.ph}", (*values.values(), now_utc(), pid)):
        return {"ok": False, "error": "To'lov yangilanmadi"}
    result = _payment_result(st, company, values, "yangilandi")
    result["id"] = pid
    result["message"] += ". " + _EXPIRY_NOTICE
    return result


def _payment_result(st, company: dict, values: dict, action: str) -> dict:
    """Yakuniy davr to'langan bo'lsa muddatni faqat uzaytiradi."""
    cid = int(company["id"])
    period, amount = values["period"], values["amount"]
    total = round(_totals(st, cid).get(cid, {}).get(period, 0.0), 2)
    try:
        price = float(company.get("billing_amount") or 0)
    except (TypeError, ValueError):
        price = 0.0
    result = {"ok": True, "period": period, "paid": total, "price": price,
              "message": f"To'lov {action}: {amount:,.0f} so'm ({period})"}
    fully_paid = price > 0 and total + 0.009 >= price
    if fully_paid:
        end = period_end(period)
        cur = _parse_date(company.get("subscription_expire_date"))
        if not cur or cur < end:
            status = str(company.get("status") or "").strip().lower()
            new_status = "active" if status in ("", "expired") else status
            from ..db.models import now_utc
            if st._exec(
                    f"UPDATE companies SET subscription_expire_date = {st.db.ph}, "
                    f"status = {st.db.ph}, updated_at = {st.db.ph} WHERE id = {st.db.ph}",
                    (end.isoformat(), new_status, now_utc(), cid)):
                from ..core import tenant
                tenant.reset()
                result["expire_date"] = end.isoformat()
                result["message"] += f" — obuna {end.isoformat()} gacha uzaytirildi"
            else:
                result["warning"] = (
                    "To'lov saqlandi, lekin obuna muddati uzaytirilmadi (DB xatosi). "
                    "Korxona sozlamalarida muddatni qo'lda to'g'rilang."
                )
                result["message"] += ". " + result["warning"]
    if not fully_paid:
        result["message"] += " (davr hali to'liq to'lanmagan)"
    return result


def delete_payment(payment_id: int, storage=None) -> dict:
    """To'lov yozuvini o'chiradi (xato kiritilgan bo'lsa).

    Obuna muddati qisqartirilmaydi — muddatni faqat admin korxona
    sozlamasida qo'lda o'zgartiradi.
    """
    st = _storage(storage)
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    try:
        pid = _positive_id(payment_id, "To'lov")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not st._exec(f"DELETE FROM company_payments WHERE id = {st.db.ph}", (pid,)):
        return {"ok": False, "error": "To'lov topilmadi"}
    return {"ok": True, "message": "To'lov o'chirildi. " + _EXPIRY_NOTICE}


def due_companies(storage=None, today: date | None = None) -> list[dict]:
    """Eslatma kerak bo'lgan korxonalar (due/grace/blocked yoki joriy oy qarzi)."""
    today = today or date.today()
    data = list_billing(storage=storage, today=today)
    out: list[dict] = []
    for c in data.get("companies", []):
        if c["state"] == "deleted":
            continue
        try:
            billing_day = int(c.get("billing_day") or REMIND_DAYS)
        except (TypeError, ValueError):
            billing_day = REMIND_DAYS
        current_unpaid = (c["debt"] > 0 and today.day >= max(1, billing_day))
        if c["state"] in ("due", "grace", "blocked") or current_unpaid:
            out.append(c)
    return out


def grace_last_day(raw) -> date | None:
    """Obuna tugash sanasi + grace (blok kuni)."""
    end = _parse_date(raw)
    return end + timedelta(days=GRACE_DAYS) if end else None
