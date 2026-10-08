"""GPS km ↔ qo'lda kiritilgan km solishtirmasi (nazorat).

`gps_daily` (GPS o'lchagan kunlik masofa) va `driver_work_logs`
(note <> 'AVTO' — qo'lda kiritilgan km yozuvlari) ni (vehicle_id, kun)
kalitida birlashtiradi. Faqat O'QISH — mavjud jadvallar o'zgartirilmaydi.

Kunlar: gps_daily — UTC kun, driver_work_logs — Toshkent sanasi; kunduzgi
reyslarda ikkisi mos tushadi (Toshkent = UTC+5).
"""

from __future__ import annotations

import logging

from ..db.storage import get_storage

log = logging.getLogger(__name__)


def _day_str(v) -> str:
    return str(v)[:10] if v is not None else ""


def _row(vid: str, plate: str, imei: str, day: str, gps_km: float,
         manual_km: float, entries: int, points: int) -> dict:
    diff = manual_km - gps_km
    pct = round(diff / gps_km * 100.0, 1) if gps_km > 0.05 else None
    return {
        "vehicle_id": vid,
        "plate": str(plate or ""),
        "imei": str(imei or ""),
        "day": day,
        "gps_km": round(gps_km, 1),
        "manual_km": round(manual_km, 1),
        "diff_km": round(diff, 1),
        "diff_pct": pct,
        "manual_entries": entries,
        "gps_points": points,
    }


def daily_comparison(date_from: str, date_to: str,
                     scope_vehicles: set | None = None) -> list[dict]:
    """(qurilma, kun) bo'yicha GPS km vs qo'lda km ro'yxati.

    scope_vehicles — None = hammasi (ADMIN); set = faqat shu vehicle_id'lar
    (kompaniya qamrovi, fail-closed).
    """
    st = get_storage()
    if not st.enabled or not getattr(st, "db", None):
        return []
    ph = st.db.ph
    try:
        gps_rows = st.db.query(
            f"""
            SELECT gd.imei, gd.day, gd.distance_m, gd.points,
                   d.vehicle_id, v.plate_number
            FROM gps_daily gd
            JOIN gps_devices d ON d.imei = gd.imei
            LEFT JOIN vehicles v ON v.external_id = d.vehicle_id
            WHERE gd.day >= {ph} AND gd.day <= {ph}
            ORDER BY gd.day DESC
            """,
            (date_from, date_to),
            limit=20000,
        )
        manual_rows = st.db.query(
            f"""
            SELECT vehicle_id, date, SUM(distance_km) AS km,
                   COUNT(*) AS entries
            FROM driver_work_logs
            WHERE date >= {ph} AND date <= {ph}
              AND COALESCE(note, '') <> {ph}
              AND COALESCE(vehicle_id, '') <> ''
            GROUP BY vehicle_id, date
            """,
            (date_from, date_to, "AVTO"),
            limit=20000,
        )
    except Exception:  # noqa: BLE001
        log.exception("compare: so'rov xatosi (%s..%s)", date_from, date_to)
        return []

    manual_map: dict[tuple[str, str], dict] = {}
    for r in manual_rows:
        key = (str(r.get("vehicle_id") or ""), _day_str(r.get("date")))
        agg = manual_map.setdefault(key, {"km": 0.0, "entries": 0})
        agg["km"] += float(r.get("km") or 0)
        agg["entries"] += int(r.get("entries") or 0)

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in gps_rows:
        vid = str(r.get("vehicle_id") or "")
        if not vid:
            continue  # avtobusga bog'lanmagan qurilma — taqqoslab bo'lmaydi
        if scope_vehicles is not None and vid not in scope_vehicles:
            continue
        day = _day_str(r.get("day"))
        key = (vid, day)
        seen.add(key)
        m = manual_map.get(key, {})
        out.append(_row(
            vid, r.get("plate_number"), r.get("imei"), day,
            float(r.get("distance_m") or 0) / 1000.0,
            float(m.get("km") or 0.0), int(m.get("entries") or 0),
            int(r.get("points") or 0),
        ))
    # Qo'lda yozuv bor, GPS kunlik yig'ama yo'q (qurilma jim / o'rnatilmagan)
    for (vid, day), m in manual_map.items():
        if (vid, day) in seen:
            continue
        if scope_vehicles is not None and vid not in scope_vehicles:
            continue
        out.append(_row(vid, "", "", day, 0.0, float(m["km"]),
                        int(m["entries"]), 0))

    _fill_plates(out, st)
    out.sort(key=lambda r: (r["day"], abs(r["diff_km"])), reverse=True)
    return out[:1000]


def _fill_plates(rows: list[dict], st) -> None:
    """Bo'sh plate'larni vehicles jadvalidan to'ldiradi (bitta so'rov)."""
    missing = {r["vehicle_id"] for r in rows if not r.get("plate")}
    if not missing:
        return
    try:
        ph = ", ".join(st.db.ph for _ in missing)
        vrows = st.db.query(
            f"SELECT external_id, plate_number FROM vehicles "
            f"WHERE external_id IN ({ph})",
            tuple(missing),
        )
        plates = {str(v.get("external_id") or ""): str(v.get("plate_number") or "")
                  for v in vrows}
        for r in rows:
            if not r.get("plate"):
                r["plate"] = plates.get(r["vehicle_id"], "")
    except Exception:  # noqa: BLE001
        log.exception("compare: plate'lar to'ldirilmadi")


def flagged_rows(rows: list[dict], tol_pct: int,
                 min_declared_km: float = 0.5,
                 no_decl_gps_km: float = 20.0,
                 no_gps_km: float = 0.5) -> list[tuple[dict, str]]:
    """Nazorat uchun "e'tibor talab" qatorlar: (row, kind) ro'yxati.

    kind: over (qo'lda ko'p) | under (qo'lda kam) | no_gps (GPS yo'q) |
    no_decl (GPS yurgan, qayd yo'q).
    """
    out: list[tuple[dict, str]] = []
    for r in rows:
        g, m = float(r.get("gps_km") or 0), float(r.get("manual_km") or 0)
        if m <= min_declared_km and g < no_decl_gps_km:
            continue  # ikkisi ham deyarli yo'q — shovqin
        if m > min_declared_km and g <= no_gps_km:
            out.append((r, "no_gps"))
        elif m <= min_declared_km and g >= no_decl_gps_km:
            out.append((r, "no_decl"))
        elif r.get("diff_pct") is not None and abs(r["diff_pct"]) >= tol_pct:
            out.append((r, "over" if r["diff_pct"] > 0 else "under"))
    return out
