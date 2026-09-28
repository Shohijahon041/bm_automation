"""GPS servis: TCP listener (FMB920) + HTTP API + DB saqlash.

Mavjud Storage'dan foydalanadi (kodiga tegmaydi) va FAQAT o'z jadvallarini
yaratadi/o'zgartiradi. Mavjud jadvallar faqat o'qiladi.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from ..db.storage import get_storage
from . import config

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gps_devices (
    imei TEXT PRIMARY KEY,
    vehicle_id TEXT DEFAULT '',
    name TEXT DEFAULT '',
    model TEXT DEFAULT 'FMB920',
    last_seen TIMESTAMPTZ,
    last_lat DOUBLE PRECISION,
    last_lng DOUBLE PRECISION,
    last_speed INTEGER DEFAULT 0,
    last_ignition INTEGER DEFAULT 0,
    last_satellites INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS gps_positions (
    id BIGSERIAL PRIMARY KEY,
    imei TEXT NOT NULL,
    vehicle_id TEXT DEFAULT '',
    ts TIMESTAMPTZ NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    altitude INTEGER DEFAULT 0,
    angle INTEGER DEFAULT 0,
    satellites INTEGER DEFAULT 0,
    speed INTEGER DEFAULT 0,
    ignition INTEGER DEFAULT 0,
    movement INTEGER DEFAULT 0,
    io JSONB,
    ts_ingested TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gps_pos_imei_ts ON gps_positions (imei, ts DESC);
CREATE INDEX IF NOT EXISTS idx_gps_pos_ts ON gps_positions (ts);
CREATE TABLE IF NOT EXISTS video_cameras (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT DEFAULT 'hls',
    url TEXT NOT NULL,
    vehicle_id TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS gps_geofences (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    radius_m INTEGER DEFAULT 500,
    vehicle_id TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS gps_alerts (
    id BIGSERIAL PRIMARY KEY,
    imei TEXT NOT NULL,
    vehicle_id TEXT DEFAULT '',
    kind TEXT NOT NULL,
    fence_id INTEGER,
    message TEXT DEFAULT '',
    ts TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gps_alerts_ts ON gps_alerts (ts DESC);
CREATE TABLE IF NOT EXISTS gps_daily (
    imei TEXT NOT NULL,
    day DATE NOT NULL,
    points INTEGER DEFAULT 0,
    distance_m DOUBLE PRECISION DEFAULT 0,
    max_speed INTEGER DEFAULT 0,
    avg_speed REAL DEFAULT 0,
    moving_points INTEGER DEFAULT 0,
    first_ts TIMESTAMPTZ,
    last_ts TIMESTAMPTZ,
    PRIMARY KEY (imei, day)
);
"""


def _st():
    """Storage (singleton)."""
    return get_storage()


def init_schema() -> bool:
    """GPS jadvallarini yaratadi. DB o'chirilgan bo'lsa False."""
    st = _st()
    if not st.enabled:
        log.error("GPS: DB o'chirilgan — pozitsiyalar saqlanmaydi")
        return False
    for stmt in [s.strip() for s in _SCHEMA.split(";") if s.strip()]:
        st.db.execute(stmt)
    log.info("GPS: sxema tayyor (gps_devices, gps_positions, video_cameras)")
    return True


def purge_old_positions() -> int:
    """Retensiya: eski pozitsiyalarni o'chiradi. O'chirilganlar soni."""
    days = config.GPS_RETENTION_DAYS
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    st = _st()
    if not st.enabled:
        return 0
    st.db.execute("DELETE FROM gps_positions WHERE ts < %s", (cutoff,))
    log.info("GPS: %s kundan eski pozitsiyalar tozalandi", days)
    return 0  # rowcount umumiy emas, hisob kitob shart emas


# ------------------------------------------------------------------ devices

def upsert_device_position(
    imei: str,
    ts: datetime,
    lat: float,
    lng: float,
    speed: int,
    ignition: int,
    satellites: int,
) -> None:
    """Qurilmaning oxirgi holatini yangilaydi (yangi IMEI bo'lsa yaratadi)."""
    st = _st()
    if not st.enabled:
        return
    st.db.execute(
        """
        INSERT INTO gps_devices
            (imei, last_seen, last_lat, last_lng, last_speed,
             last_ignition, last_satellites)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (imei) DO UPDATE SET
            last_seen = EXCLUDED.last_seen,
            last_lat = EXCLUDED.last_lat,
            last_lng = EXCLUDED.last_lng,
            last_speed = EXCLUDED.last_speed,
            last_ignition = EXCLUDED.last_ignition,
            last_satellites = EXCLUDED.last_satellites
        """,
        (imei, ts, lat, lng, speed, ignition, satellites),
    )


def device_known(imei: str) -> bool:
    st = _st()
    if not st.enabled:
        return config.GPS_AUTO_REGISTER
    rows = st.db.query("SELECT 1 FROM gps_devices WHERE imei = %s", (imei,),
                       limit=1)
    return bool(rows)


def register_device(imei: str) -> None:
    """Yangi IMEI'ni avtomatik ro'yxatga oladi (AUTO_REGISTER=1 bo'lsa)."""
    st = _st()
    if not st.enabled:
        return
    st.db.execute(
        "INSERT INTO gps_devices (imei) VALUES (%s) ON CONFLICT DO NOTHING",
        (imei,),
    )


def device_allowed(imei: str) -> bool:
    """Whitelist yoki auto-register siyosatiga mosligini tekshiradi."""
    if config.GPS_IMEI_WHITELIST:
        return imei in config.GPS_IMEI_WHITELIST
    return config.GPS_AUTO_REGISTER or device_known(imei)


# --------------------------------------------------------------- positions

def insert_positions(imei: str, records: list) -> int:
    """AVL recordlarni gps_positions'ga yozadi. Yozilganlar soni."""
    st = _st()
    if not st.enabled or not records:
        return 0
    vid = _vehicle_id_for(imei)
    n = 0
    for r in records:
        st.db.execute(
            """
            INSERT INTO gps_positions
                (imei, vehicle_id, ts, lat, lng, altitude, angle,
                 satellites, speed, ignition, movement, io)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                imei, vid, r.timestamp, r.latitude, r.longitude,
                r.altitude, r.angle, r.satellites, r.speed,
                r.ignition, r.movement,
                json.dumps(r.io, ensure_ascii=False) if r.io else None,
            ),
        )
        n += 1
    return n


def _vehicle_id_for(imei: str) -> str:
    st = _st()
    if not st.enabled:
        return ""
    rows = st.db.query(
        "SELECT vehicle_id FROM gps_devices WHERE imei = %s", (imei,), limit=1
    )
    return str(rows[0].get("vehicle_id") or "") if rows else ""


def list_devices() -> list:
    """Qurilmalar + avtobus raqami (mavjud vehicles jadvalidan O'QISH)."""
    st = _st()
    if not st.enabled:
        return []
    rows = st.db.query(
        """
        SELECT d.imei, d.vehicle_id, d.name, d.model, d.last_seen,
               d.last_lat, d.last_lng, d.last_speed, d.last_ignition,
               d.last_satellites, d.enabled,
               v.plate_number
        FROM gps_devices d
        LEFT JOIN vehicles v ON v.external_id = d.vehicle_id
        ORDER BY d.imei
        """
    )
    now = time.time()
    for r in rows:
        # online: oxirgi ko'rinish ONLINE_WINDOW ichida
        ls = r.get("last_seen")
        online = False
        if ls:
            try:
                ts = ls if isinstance(ls, datetime) else datetime.fromisoformat(
                    str(ls).replace("Z", "+00:00")
                )
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                online = (datetime.now(timezone.utc) - ts).total_seconds() <= (
                    config.GPS_ONLINE_WINDOW_S
                )
            except Exception:  # noqa: BLE001
                online = False
        r["online"] = online and bool(r.get("enabled"))
        r["last_seen_s"] = ls.isoformat() if isinstance(ls, datetime) else (
            str(ls) if ls else ""
        )
        r["_now"] = now  # debugging uchun emas — API'da ishlatilmaydi
        del r["_now"]
    return rows


def positions(
    imei: str = "",
    vehicle_id: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 5000,
) -> list:
    """Traektoriya so'rovi (sana YYYY-MM-DD yoki to'liq ISO)."""
    st = _st()
    if not st.enabled:
        return []
    where, params = [], []
    if imei:
        where.append("imei = %s")
        params.append(imei)
    if vehicle_id:
        where.append("vehicle_id = %s")
        params.append(vehicle_id)
    if date_from:
        where.append("ts >= %s")
        params.append(_iso(date_from, start=True))
    if date_to:
        where.append("ts <= %s")
        params.append(_iso(date_to, start=False))
    sql = (
        "SELECT imei, vehicle_id, ts, lat, lng, altitude, angle, "
        "satellites, speed, ignition, movement FROM gps_positions"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts ASC LIMIT %s"
    params.append(max(1, min(int(limit), 50000)))
    rows = st.db.query(sql, tuple(params))
    out = []
    for r in rows:
        r["ts"] = r["ts"].isoformat() if isinstance(r["ts"], datetime) else str(r["ts"])
        out.append(r)
    return out


def _iso(date_str: str, start: bool) -> str:
    """YYYY-MM-DD → ISO davr boshi/oxiri (UTC)."""
    s = str(date_str).strip()
    if len(s) == 10:
        return f"{s}T00:00:00Z" if start else f"{s}T23:59:59Z"
    return s


# ------------------------------------------------------------------ cameras

def list_cameras() -> list:
    st = _st()
    if not st.enabled:
        return []
    return st.db.query(
        "SELECT id, name, source_type, url, vehicle_id, enabled "
        "FROM video_cameras ORDER BY id"
    )


def add_camera(name: str, url: str, source_type: str = "hls",
               vehicle_id: str = "") -> int:
    st = _st()
    if not st.enabled:
        return 0
    st.db.execute(
        "INSERT INTO video_cameras (name, source_type, url, vehicle_id) "
        "VALUES (%s,%s,%s,%s)",
        (name, source_type, url, vehicle_id),
    )
    rows = st.db.query("SELECT MAX(id) AS id FROM video_cameras", limit=1)
    return int(rows[0]["id"]) if rows else 0


def delete_camera(camera_id: int) -> None:
    st = _st()
    if not st.enabled:
        return
    st.db.execute("DELETE FROM video_cameras WHERE id = %s", (camera_id,))


def bind_device(imei: str, vehicle_id: str, name: str = "") -> bool:
    st = _st()
    if not st.enabled:
        return False
    st.db.execute(
        "UPDATE gps_devices SET vehicle_id = %s, name = COALESCE("
        "NULLIF(%s, ''), name) WHERE imei = %s",
        (vehicle_id, name, imei),
    )
    return True


def set_device_enabled(imei: str, enabled: bool) -> bool:
    st = _st()
    if not st.enabled:
        return False
    st.db.execute(
        "UPDATE gps_devices SET enabled = %s WHERE imei = %s",
        (1 if enabled else 0, imei),
    )
    return True


# ---------------------------------------------------------------- geofence

def list_geofences(only_enabled: bool = False) -> list:
    st = _st()
    if not st.enabled:
        return []
    sql = ("SELECT id, name, lat, lng, radius_m, vehicle_id, enabled "
           "FROM gps_geofences")
    if only_enabled:
        sql += " WHERE enabled = 1"
    return st.db.query(sql + " ORDER BY id")


def add_geofence(name: str, lat: float, lng: float, radius_m: int,
                 vehicle_id: str = "") -> int:
    st = _st()
    if not st.enabled:
        return 0
    st.db.execute(
        "INSERT INTO gps_geofences (name, lat, lng, radius_m, vehicle_id) "
        "VALUES (%s,%s,%s,%s,%s)",
        (name, lat, lng, radius_m, vehicle_id),
    )
    rows = st.db.query("SELECT MAX(id) AS id FROM gps_geofences", limit=1)
    return int(rows[0]["id"]) if rows else 0


def delete_geofence(fence_id: int) -> None:
    st = _st()
    if not st.enabled:
        return
    st.db.execute("DELETE FROM gps_geofences WHERE id = %s", (fence_id,))


def set_geofence_enabled(fence_id: int, enabled: bool) -> None:
    st = _st()
    if not st.enabled:
        return
    st.db.execute(
        "UPDATE gps_geofences SET enabled = %s WHERE id = %s",
        (1 if enabled else 0, fence_id),
    )


def insert_alert(imei: str, vehicle_id: str, kind: str, fence_id: int,
                 message: str) -> None:
    st = _st()
    if not st.enabled:
        return
    st.db.execute(
        "INSERT INTO gps_alerts (imei, vehicle_id, kind, fence_id, message) "
        "VALUES (%s,%s,%s,%s,%s)",
        (imei, vehicle_id, kind, fence_id, message),
    )


def list_alerts(limit: int = 50, imei: str = "") -> list:
    st = _st()
    if not st.enabled:
        return []
    sql = ("SELECT id, imei, vehicle_id, kind, fence_id, message, ts "
           "FROM gps_alerts")
    params: tuple = ()
    if imei:
        sql += " WHERE imei = %s"
        params = (imei,)
    sql += " ORDER BY ts DESC LIMIT %s"
    rows = st.db.query(sql, params + (max(1, min(int(limit), 200)),))
    for r in rows:
        r["ts"] = (r["ts"].isoformat()
                    if isinstance(r["ts"], datetime) else str(r["ts"]))
    return rows


# ------------------------------------------------------------------ daily

def daily(imei: str = "", date_from: str = "", date_to: str = "") -> list:
    st = _st()
    if not st.enabled:
        return []
    where, params = [], []
    if imei:
        where.append("imei = %s")
        params.append(imei)
    if date_from:
        where.append("day >= %s")
        params.append(date_from)
    if date_to:
        where.append("day <= %s")
        params.append(date_to)
    sql = ("SELECT imei, day, points, distance_m, max_speed, avg_speed, "
           "moving_points, first_ts, last_ts FROM gps_daily")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY day DESC, imei LIMIT 1000"
    rows = st.db.query(sql, tuple(params))
    for r in rows:
        r["day"] = str(r["day"])
        for k in ("first_ts", "last_ts"):
            r[k] = (r[k].isoformat()
                    if isinstance(r[k], datetime) else (str(r[k]) if r[k] else None))
    return rows
