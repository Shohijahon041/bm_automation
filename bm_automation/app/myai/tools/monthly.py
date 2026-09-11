"""Monthly Tool — oylik haydovchi va yo'nalish statistikasi.

Dashboard Metrics (drivers / _single_driver_metrics / monthly) bilan bir xil
mantiqni ishlatadi, shuning uchun agent natijalari botdagi "Oylik" hisoblari
bilan mos tushadi: km, ish kunlari, davomat, gross/net maosh, soliq, jarimalar.
"""

from __future__ import annotations

from typing import Any

from . import BaseTool
from ...dashboard.metrics import Metrics
from ...utils.logger import get_logger
from ...utils.names import short_name

log = get_logger("myai.tools.monthly")

# drivers() qaytaradigan yig'mali maydonlardan agent javobiga keraklilari
_DRIVER_FIELDS = (
    "driver_id", "name", "company", "route_id", "route_name",
    "trips", "manual_trips", "working_days", "attendance", "total_days",
    "issues", "km", "km_rate", "gross_pay", "tax", "fines", "net_pay",
    "rating", "blacklisted",
)

_SORT_KEYS = {
    "km": "km",
    "gross": "gross_pay",
    "net": "net_pay",
    "maosh": "net_pay",
    "salary": "net_pay",
    "rating": "rating",
    "baho": "rating",
    "trips": "trips",
    "qatnov": "trips",
    "attendance": "attendance",
    "davomat": "attendance",
    "days": "working_days",
    "issues": "issues",
}


class MonthlyTool(BaseTool):
    """Oylik (monthly) ma'lumot — haydovchi/yo'nalish bo'yicha."""

    name = "monthly"
    description = (
        "Oylik admin statistika: get_driver_monthly (bitta haydovchi), "
        "get_drivers_monthly (rejting/summa), get_monthly_summary (oy xulosasi) "
        "— km, ish kunlari, davomat, gross/tax/net maosh, jarimalar, baho"
    )

    async def execute(self, action: str = "", **kwargs) -> Any:
        actions = {
            "get_driver_monthly": self._get_driver_monthly,
            "get_drivers_monthly": self._get_drivers_monthly,
            "get_monthly_summary": self._get_monthly_summary,
        }
        handler = actions.get(action)
        if handler is None:
            raise ValueError(f"Noma'lum monthly action: {action}")
        return await handler(**kwargs)

    @staticmethod
    def _filters(month: str = "", **kwargs) -> dict:
        """month ("2026-08") yoki from/to asosida Metrics filter'ini yig'adi."""
        f: dict[str, Any] = {}
        if str(month or "").strip():
            f["month"] = str(month).strip()
        else:
            frm = str(kwargs.get("from") or "").strip()
            to = str(kwargs.get("to") or "").strip()
            if frm:
                f["from"] = frm
            if to:
                f["to"] = to
        for key in ("route", "vehicle", "status", "driver"):
            val = str(kwargs.get(key) or "").strip()
            if val:
                f[key] = val
        if not f:
            from datetime import date as _date
            f["month"] = _date.today().strftime("%Y-%m")
        return f

    async def _resolve_route(self, route_id: str = "") -> str:
        """Yo'nalish nomini ("B-80") UUID ga bog'laydi; UUID bo'lsa qaytaradi."""
        route_id = (route_id or "").strip()
        if not route_id:
            return ""
        if "-" in route_id and len(route_id) == 36:
            return route_id
        m = Metrics()
        rows = m.storage.query(
            "SELECT external_id FROM routes "
            "WHERE LOWER(name) = LOWER(%s) LIMIT 1", (route_id,))
        if not rows:
            rows = m.storage.query(
                "SELECT external_id FROM routes "
                "WHERE LOWER(name) LIKE LOWER(%s) AND entity_type = 'ROUTE' LIMIT 1",
                (f"%{route_id}%",))
        return str(rows[0]["external_id"]) if rows else route_id

    @staticmethod
    def _label(f: dict) -> str:
        if f.get("month"):
            return str(f["month"])
        frm = f.get("from", "?")
        to = f.get("to", frm)
        return f"{frm}…{to}"

    def _norm_driver(self, row: dict) -> dict:
        out = {k: row.get(k) for k in _DRIVER_FIELDS
               if k in row and row[k] is not None}
        # Bot javoblarida faqat ism+familya ko'rsatiladi (dashboard to'liq).
        if out.get("name"):
            out["name"] = short_name(str(out["name"]))
        return out

    async def _resolve_driver(self, driver: str = "") -> tuple[str, str]:
        """Haydovchi ismini external_id (va to'liq ism) ga bog'laydi."""
        driver = (driver or "").strip()
        if not driver:
            return "", ""
        m = Metrics()
        rows = m.storage.query(
            "SELECT external_id, full_name FROM drivers "
            "WHERE LOWER(full_name) = LOWER(%s) LIMIT 1", (driver,))
        if not rows:
            rows = m.storage.query(
                "SELECT external_id, full_name FROM drivers "
                "WHERE LOWER(full_name) LIKE LOWER(%s) "
                "OR LOWER(external_id) = LOWER(%s) LIMIT 1",
                (f"%{driver}%", driver))
        if rows:
            row = rows[0]
            return str(row["external_id"]), str(row.get("full_name") or driver)
        return "", driver

    async def _get_driver_monthly(
        self, driver: str = "", driver_id: str = "", month: str = "", **kwargs
    ) -> dict:
        m = Metrics()
        f = self._filters(month=month, **kwargs)
        did = str(driver_id or "").strip()
        full_name = driver
        if not did:
            did, full_name = await self._resolve_driver(driver)
        if not did:
            return {"found": False, "query": driver or "",
                    "message": "Haydovchi topilmadi"}
        try:
            period = m._driver_period(f)
            data = m._single_driver_metrics(did, period)
        except Exception as exc:  # noqa: BLE001
            log.warning("get_driver_monthly xatosi (%s): %s", did, exc)
            return {"found": False, "driver_id": did, "error": str(exc)}
        if data is None:
            return {"found": False, "driver_id": did,
                    "query": full_name or did, "period": self._label(f),
                    "message": "Bu davrda haydovchi ma'lumoti yo'q"}
        norm = self._norm_driver(data)
        norm["found"] = True
        norm["query"] = full_name or norm.get("name", did)
        norm["period"] = self._label(f)
        return norm

    async def _get_drivers_monthly(
        self, route_id: str = "", driver: str = "", month: str = "",
        sort: str = "net_pay", top: int = 0, **kwargs
    ) -> dict:
        m = Metrics()
        f = self._filters(month=month, **kwargs)
        rroute = await self._resolve_route(
            str(route_id or "").strip() or str(kwargs.get("route") or "").strip())
        if rroute:
            f["route"] = rroute
        try:
            rows = m.drivers(f)
        except Exception as exc:  # noqa: BLE001
            log.warning("get_drivers_monthly xatosi: %s", exc)
            return {"found": False, "error": str(exc)}

        norm = [self._norm_driver(r) for r in rows]
        totals = {
            "drivers": len(norm),
            "active_drivers": sum(1 for r in norm if r.get("working_days", 0) > 0),
            "total_trips": sum(int(r.get("trips", 0)) + int(r.get("manual_trips", 0))
                               for r in norm),
            "total_km": round(sum(float(r.get("km") or 0) for r in norm), 2),
            "total_gross": round(sum(float(r.get("gross_pay") or 0) for r in norm), 2),
            "total_tax": round(sum(float(r.get("tax") or 0) for r in norm), 2),
            "total_fines": round(sum(float(r.get("fines") or 0) for r in norm), 2),
            "total_net": round(sum(float(r.get("net_pay") or 0) for r in norm), 2),
        }

        key = _SORT_KEYS.get(str(sort or "net_pay").strip().lower(), "net_pay")
        ranked = sorted(norm, key=lambda r: (r.get(key) is None, r.get(key) or 0),
                        reverse=True)
        if top and int(top) > 0:
            ranked = ranked[:int(top)]
        return {
            "found": bool(norm),
            "period": self._label(f),
            "count": len(ranked),
            "total_drivers": totals["drivers"],
            "sorted_by": key,
            "totals": totals,
            "drivers": ranked,
        }

    async def _get_monthly_summary(self, month: str = "", route_id: str = "",
                                   **kwargs) -> dict:
        m = Metrics()
        f = self._filters(month=month, **kwargs)
        rroute = await self._resolve_route(
            str(route_id or "").strip() or str(kwargs.get("route") or "").strip())
        if rroute:
            f["route"] = rroute
        try:
            mon = m.monthly({**f, "date": "", "from": "", "to": ""})
        except Exception as exc:  # noqa: BLE001
            log.warning("get_monthly_summary xatosi: %s", exc)
            return {"found": False, "error": str(exc)}

        days = mon.get("days") or []
        t = mon.get("totals", {})
        # Haydovchi tomon: faol haydovchilar, km, net maosh
        try:
            dr = m.drivers(f)
        except Exception:  # noqa: BLE001
            dr = []
        d_active = sum(1 for r in dr if r.get("working_days", 0) > 0)
        d_km = round(sum(float(r.get("km") or 0) for r in dr), 2)
        d_net = round(sum(float(r.get("net_pay") or 0) for r in dr), 2)

        return {
            "found": bool(days),
            "period": self._label(f),
            "days": len(days),
            "totals": {
                "planned": t.get("planned", 0),
                "total": t.get("total", 0),
                "accepted": t.get("accepted", 0),
                "not_accepted": t.get("not_accepted", 0),
                "rejected": t.get("rejected", 0),
                "zero_mileage": t.get("zero_mileage", 0),
                "problems_total": t.get("problems_total", 0),
                "performance": t.get("performance", 0.0),
                "accept_rate": t.get("accept_rate", 0.0),
            },
            "drivers": {
                "total": len(dr),
                "active": d_active,
                "km": d_km,
                "net_pay": d_net,
            },
        }