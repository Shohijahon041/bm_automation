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
    kind TEXT DEFAULT 'teltonika',
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
    created_at TIMESTAMPTZ DEFAULT now(),
    -- Camera health monitoring
    last_check_at TIMESTAMPTZ,
    last_online_at TIMESTAMPTZ,
    consecutive_failures INTEGER DEFAULT 0,
    total_failures INTEGER DEFAULT 0,
    last_error TEXT DEFAULT '',
    last_fps REAL DEFAULT 0,
    last_bitrate INTEGER DEFAULT 0,
    last_resolution TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS video_audit (
    id BIGSERIAL PRIMARY KEY,
    camera_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    action TEXT NOT NULL,  -- grid, focus, fullscreen
    channel INTEGER NOT NULL DEFAULT 1,
    vehicle_id TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    duration_s INTEGER DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS gps_route_paths (
    route_id TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    points JSONB NOT NULL,
    tolerance_m INTEGER DEFAULT 200,
    enabled INTEGER DEFAULT 1,
    updated_at TIMESTAMPTZ DEFAULT now()
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
    _migrate()
    log.info("GPS: sxema tayyor (gps_devices, gps_positions, video_cameras)")
    return True


def _migrate() -> None:
    """Eski bazalarga ustun qo'shadi (idempotent).

    `gps_devices.kind` — 'teltonika' (xarita uchun haqiqiy GPS) yoki
    'camera' (JT808/JT1078 kamera: faqat video/yordamchi, xaritaga
    chiqmaydi). Eski qatorlar 'teltonika' bo'lib qoladi.

    Bundan tashqari 12 xonali IMEI'li qatorlar (JT808 kamera BCD 6 bayt)
    'camera' deb belgilanadi — ilgari noto'g'ri 'teltonika' bo'lib yozilgan
    bo'lsa tuzatiladi. Teltonika IMEI 15 xonali, shu sababli tegilmaydi.

    `video_cameras` — health ustunlari (last_check_at va h.k.) eski
    bazalarda yo'q bo'ladi, qo'shib qo'yiladi.
    """
    st = _st()
    try:
        if getattr(st.db, "driver", "") == "sqlite":
            cols = {str(r.get("name") or "")
                    for r in st.db.query("PRAGMA table_info(gps_devices)")}
            if "kind" not in cols:
                st.db.execute(
                    "ALTER TABLE gps_devices ADD COLUMN kind TEXT "
                    "DEFAULT 'teltonika'")
        else:
            st.db.execute(
                "ALTER TABLE gps_devices ADD COLUMN IF NOT EXISTS "
                "kind TEXT DEFAULT 'teltonika'")
    except Exception:  # noqa: BLE001
        log.exception("GPS: gps_devices.kind migratsiyasi bajarilmadi")

    try:
        n = st.db.execute(
            "UPDATE gps_devices SET kind = 'camera' "
            "WHERE length(imei) = 12 AND COALESCE(kind, '') <> 'camera'")
        if n:
            log.info("GPS: %s ta 12-xonali IMEI 'camera' deb belgilandi", n)
    except Exception:  # noqa: BLE001
        log.exception("GPS: 12-xonali IMEI -> camera migratsiyasi bajarilmadi")

    try:
        health_cols = (
            ("last_check_at", "TIMESTAMPTZ"),
            ("last_online_at", "TIMESTAMPTZ"),
            ("consecutive_failures", "INTEGER DEFAULT 0"),
            ("total_failures", "INTEGER DEFAULT 0"),
            ("last_error", "TEXT DEFAULT ''"),
            ("last_fps", "REAL DEFAULT 0"),
            ("last_bitrate", "INTEGER DEFAULT 0"),
            ("last_resolution", "TEXT DEFAULT ''"),
        )
        if getattr(st.db, "driver", "") == "sqlite":
            have = {str(r.get("name") or "")
                    for r in st.db.query("PRAGMA table_info(video_cameras)")}
            if have:
                for col, typ in health_cols:
                    if col not in have:
                        st.db.execute(
                            "ALTER TABLE video_cameras ADD COLUMN "
                            + col + " " + typ)
        else:
            for col, typ in health_cols:
                st.db.execute(
                    "ALTER TABLE video_cameras ADD COLUMN IF NOT EXISTS "
                    + col + " " + typ)
    except Exception:  # noqa: BLE001
        log.exception("GPS: video_cameras health ustunlari migratsiyasi bajarilmadi")


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
    kind: str = "teltonika",
) -> None:
    """Qurilmaning oxirgi holatini yangilaydi (yangi IMEI bo'lsa yaratadi).

    kind faqat YANGI qatorda qo'llanadi — mavjud qatorning turi
    o'zgartirilmaydi (kamera qatori tasodifan Teltonika bo'lib qolmasin).
    """
    st = _st()
    if not st.enabled:
        return
    st.db.execute(
        """
        INSERT INTO gps_devices
            (imei, last_seen, last_lat, last_lng, last_speed,
             last_ignition, last_satellites, kind)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (imei) DO UPDATE SET
            last_seen = EXCLUDED.last_seen,
            last_lat = EXCLUDED.last_lat,
            last_lng = EXCLUDED.last_lng,
            last_speed = EXCLUDED.last_speed,
            last_ignition = EXCLUDED.last_ignition,
            last_satellites = EXCLUDED.last_satellites
        """,
        (imei, ts, lat, lng, speed, ignition, satellites,
         str(kind or "teltonika")),
    )


def device_known(imei: str) -> bool:
    st = _st()
    if not st.enabled:
        return config.GPS_AUTO_REGISTER
    rows = st.db.query("SELECT 1 FROM gps_devices WHERE imei = %s", (imei,),
                       limit=1)
    return bool(rows)


def register_device(imei: str, kind: str = "teltonika") -> None:
    """Yangi IMEI'ni avtomatik ro'yxatga oladi (AUTO_REGISTER=1 bo'lsa).

    kind='camera' — JT808/JT1078 kamera terminali: xaritada ko'rsatilmaydi,
    faqat video va yordamchi tahlil uchun ishlatiladi.
    """
    st = _st()
    if not st.enabled:
        return
    st.db.execute(
        "INSERT INTO gps_devices (imei, kind) VALUES (%s,%s) "
        "ON CONFLICT DO NOTHING",
        (imei, str(kind or "teltonika")),
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
        SELECT d.imei, d.vehicle_id, d.name, d.model, d.kind, d.last_seen,
               d.last_lat, d.last_lng, d.last_speed, d.last_ignition,
               d.last_satellites, d.enabled,
               v.plate_number, v.route_id AS route_id
        FROM gps_devices d
        LEFT JOIN vehicles v ON v.external_id = d.vehicle_id
        ORDER BY d.imei
        """
    )
    now = time.time()
    for r in rows:
        r["kind"] = str(r.get("kind") or "teltonika")
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


def teltonika_only(rows: list) -> list:
    """Ro'yxatdan kamera (kind='camera') qatorlarini chiqarib tashlaydi.

    Kamera GPS xarita/trek/nazoratda ishlatilmaydi — faqat video va
    yordamchi tahlil (`gps/camera.py`). Test mock'larida `kind` bo'lmasa
    qator Teltonika deb hisoblanadi (eski xatti-harakat saqlanadi).
    """
    return [r for r in (rows or [])
            if str(r.get("kind") or "teltonika") != "camera"]


def list_camera_devices() -> list:
    """Kamera terminallari (kind='camera') + avtobus raqami (O'QISH)."""
    st = _st()
    if not st.enabled:
        return []
    try:
        rows = st.db.query(
            """
            SELECT d.imei, d.vehicle_id, d.name, d.last_seen, d.last_lat,
                   d.last_lng, d.last_speed, d.enabled, d.kind,
                   v.plate_number, v.route_id AS route_id
            FROM gps_devices d
            LEFT JOIN vehicles v ON v.external_id = d.vehicle_id
            WHERE COALESCE(d.kind, 'teltonika') = 'camera'
            ORDER BY d.imei
            """
        )
    except Exception:  # noqa: BLE001
        return []
    for r in rows:
        r["kind"] = "camera"
    return rows


def bind_camera(imei: str, vehicle_id: str, name: str = "") -> bool:
    """Kamera terminalini avtobusga bog'lash (bind_device bilan bir xil)."""
    return bind_device(imei, vehicle_id, name)


def list_vehicles() -> list:
    """Mavjud avtobuslar + bog'langan GPS qurilmasi (bitta qator = bitta avtobus).

    `vehicles` jadvali BM saytidan sinxronlanadi; bog'lash `gps_devices.vehicle_id`
    orqali `vehicles.external_id` ga ulanadi. Bog'lanmagan avtobusda
    `device_imei` bo'sh qaytadi.
    """
    st = _st()
    if not st.enabled:
        return []
    rows = st.db.query(
        """
        SELECT v.external_id, v.plate_number, v.garage_number, v.model,
               v.route_id, d.imei AS device_imei, d.name AS device_name
        FROM vehicles v
        LEFT JOIN gps_devices d ON d.vehicle_id = v.external_id
        ORDER BY v.plate_number, v.external_id
        """
    )
    return [
        {
            "external_id": str(r.get("external_id") or ""),
            "plate_number": str(r.get("plate_number") or ""),
            "garage_number": str(r.get("garage_number") or ""),
            "model": str(r.get("model") or ""),
            "route_id": str(r.get("route_id") or ""),
            "device_imei": str(r.get("device_imei") or ""),
            "device_name": str(r.get("device_name") or ""),
        }
        for r in rows
    ]


def route_names(ids) -> dict:
    """route_id → nom (routes jadvalidan O'QISH). Topilmasa bo'sh."""
    clean = [str(i) for i in (ids or []) if str(i or "").strip()]
    st = _st()
    if not st.enabled or not clean:
        return {}
    out: dict = {}
    try:
        ph = ", ".join(st.db.ph for _ in clean)
        rows = st.db.query(
            f"SELECT external_id, name FROM routes WHERE external_id IN ({ph})",
            tuple(clean))
        for r in rows:
            out[str(r.get("external_id"))] = str(r.get("name") or "")
    except Exception:  # noqa: BLE001
        log.exception("GPS: route nomlari olinmadi")
    return out


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
        """SELECT id, name, source_type, url, vehicle_id, enabled,
                  last_check_at, last_online_at, consecutive_failures,
                  total_failures, last_error, last_fps, last_bitrate, last_resolution
           FROM video_cameras ORDER BY id"""
    )


def add_camera(name: str, url: str, source_type: str = "hls",
               vehicle_id: str = "") -> int:
    st = _st()
    if not st.enabled:
        return 0
    st.db.execute(
        """INSERT INTO video_cameras (name, source_type, url, vehicle_id,
              consecutive_failures, total_failures, last_error, last_fps, last_bitrate, last_resolution)
           VALUES (%s,%s,%s,%s, 0, 0, '', 0, 0, '')""",
        (name, source_type, url, vehicle_id),
    )
    rows = st.db.query("SELECT MAX(id) AS id FROM video_cameras", limit=1)
    return int(rows[0]["id"]) if rows else 0


def update_camera_health(camera_id: int, success: bool, error: str = "",
                         fps: float = 0, bitrate: int = 0, resolution: str = "") -> None:
    """Update camera health metrics after stream check."""
    st = _st()
    if not st.enabled:
        return
    now = datetime.now(timezone.utc)
    if success:
        st.db.execute(
            """UPDATE video_cameras SET
                last_check_at = %s, last_online_at = %s,
                consecutive_failures = 0, last_error = '',
                last_fps = %s, last_bitrate = %s, last_resolution = %s
                WHERE id = %s""",
            (now, now, fps, bitrate, resolution, camera_id),
        )
    else:
        st.db.execute(
            """UPDATE video_cameras SET
                last_check_at = %s,
                consecutive_failures = consecutive_failures + 1,
                total_failures = total_failures + 1,
                last_error = %s
                WHERE id = %s""",
            (now, error[:500], camera_id),
        )


def get_camera_health(camera_id: int) -> dict | None:
    """Get camera health metrics."""
    st = _st()
    if not st.enabled:
        return None
    rows = st.db.query(
        """SELECT id, name, source_type, vehicle_id, enabled,
                  last_check_at, last_online_at, consecutive_failures,
                  total_failures, last_error, last_fps, last_bitrate, last_resolution
           FROM video_cameras WHERE id = %s""",
        (camera_id,), limit=1)
    if not rows:
        return None
    return dict(rows[0])


def delete_camera(camera_id: int) -> None:
    st = _st()
    if not st.enabled:
        return
    st.db.execute("DELETE FROM video_cameras WHERE id = %s", (camera_id,))


def log_video_audit(camera_id: int, user_id: int, username: str, action: str,
                    channel: int = 1, vehicle_id: str = "", ip: str = "",
                    duration_s: int = 0) -> None:
    """Log video viewing action to audit table."""
    st = _st()
    if not st.enabled:
        return
    st.db.execute(
        """INSERT INTO video_audit (camera_id, user_id, username, action, channel, vehicle_id, ip, duration_s)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (camera_id, user_id, username, action, channel, vehicle_id, ip, duration_s)
    )


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


def delete_device(imei: str) -> bool:
    """Qurilmani ro'yxatdan butunlay o'chirish (bog'lash ham yo'qoladi)."""
    st = _st()
    if not st.enabled:
        return False
    st.db.execute("DELETE FROM gps_devices WHERE imei = %s", (imei,))
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


def update_geofence(fence_id: int, name: str, lat: float, lng: float,
                    radius_m: int, vehicle_id: str = "") -> bool:
    """Zona markazi / radiusi / nomini / avtobusini tahrirlash (koordinata bilan)."""
    st = _st()
    if not st.enabled:
        return False
    st.db.execute(
        "UPDATE gps_geofences SET name = %s, lat = %s, lng = %s, "
        "radius_m = %s, vehicle_id = %s WHERE id = %s",
        (name, lat, lng, radius_m, vehicle_id, fence_id),
    )
    return True


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


# -------------------------------------------------------------- route paths

def _route_path_row(r: dict) -> dict:
    p = r.get("points")
    if isinstance(p, str):
        try:
            r["points"] = json.loads(p)
        except Exception:  # noqa: BLE001
            r["points"] = []
    return r


def list_route_paths(only_enabled: bool = False) -> list:
    st = _st()
    if not st.enabled:
        return []
    sql = ("SELECT route_id, name, points, tolerance_m, enabled, updated_at "
           "FROM gps_route_paths")
    if only_enabled:
        sql += " WHERE enabled = 1"
    rows = st.db.query(sql + " ORDER BY route_id")
    return [_route_path_row(r) for r in rows]


def get_route_path(route_id: str) -> dict | None:
    st = _st()
    if not st.enabled:
        return None
    ph = st.db.ph
    rows = st.db.query(
        "SELECT route_id, name, points, tolerance_m, enabled "
        f"FROM gps_route_paths WHERE route_id = {ph}",
        (str(route_id or ""),), limit=1)
    return _route_path_row(rows[0]) if rows else None


def upsert_route_path(route_id: str, points: list, name: str = "",
                      tolerance_m: int = 200) -> bool:
    st = _st()
    if not st.enabled:
        return False
    ph = st.db.ph
    st.db.execute(
        "INSERT INTO gps_route_paths "
        "(route_id, name, points, tolerance_m, enabled) "
        f"VALUES ({ph},{ph},{ph},{ph},1) "
        "ON CONFLICT (route_id) DO UPDATE SET "
        "name = EXCLUDED.name, points = EXCLUDED.points, "
        "tolerance_m = EXCLUDED.tolerance_m, enabled = 1, "
        "updated_at = CURRENT_TIMESTAMP",
        (str(route_id or ""), str(name or ""), json.dumps(points),
         int(tolerance_m or 0)),
    )
    return True


def delete_route_path(route_id: str) -> None:
    st = _st()
    if not st.enabled:
        return
    ph = st.db.ph
    st.db.execute(f"DELETE FROM gps_route_paths WHERE route_id = {ph}",
                  (str(route_id or ""),))


def set_route_path_enabled(route_id: str, enabled: bool) -> None:
    st = _st()
    if not st.enabled:
        return
    ph = st.db.ph
    st.db.execute(
        f"UPDATE gps_route_paths SET enabled = {ph} WHERE route_id = {ph}",
        (1 if enabled else 0, str(route_id or "")))


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
