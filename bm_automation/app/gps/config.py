"""GPS servis — yagona modular konfiguratsiya (env).

Dashboard server.py'dagi modelga mos: bitta obyekt, start'da o'qiladi.
.env.example'ga TEGMAYMIZ — faqat GPS servisi o'zi bu qiymatlarni o'qiydi,
standart qiymatlar bilan ishlash uchun hech narsa sozlash shart emas.
"""

from __future__ import annotations

import os
import tempfile

# ---- TCP listener (Teltonika qurilmalar ulanadigan port) ----
GPS_TCP_HOST = os.environ.get("GPS_TCP_HOST", "0.0.0.0")
GPS_TCP_PORT = int(os.environ.get("GPS_TCP_PORT", "5027"))

# ---- HTTP API + kuzatuv UI (frontend uchun) ----
# 0.0.0.0 = tarmoqdan ham ochiq (telefon/planşetda video kuzatuv uchun).
# Tokenda data himoyalangan — sahifa ?token=... bilan ochiladi.
GPS_API_HOST = os.environ.get("GPS_API_HOST", "0.0.0.0")
GPS_API_PORT = int(os.environ.get("GPS_API_PORT", "8081"))

# ---- Xavfsizlik ----
# Dashboard bilan bir xil token; bo'sh bo'lsa API ochiq (faqat localhost).
GPS_API_TOKEN = (os.environ.get("DASHBOARD_TOKEN") or "").strip()

# ---- Qurilma ro'yxati ----
# 1 = yangi IMEI avtomatik ro'yxatga olinadi (frontend'da avtobusga bog'lanadi)
GPS_AUTO_REGISTER = os.environ.get("GPS_AUTO_REGISTER", "1") in ("1", "true", "yes")

# IMEI whitelist (vergul bilan); GPS_AUTO_REGISTER=0 bo'lsa faqat shular qabul.
GPS_IMEI_WHITELIST = [
    s.strip()
    for s in (os.environ.get("GPS_IMEI_WHITELIST") or "").split(",")
    if s.strip()
]

# ---- Ma'lumot saqlash ----
GPS_RETENTION_DAYS = int(os.environ.get("GPS_RETENTION_DAYS", "30"))
GPS_POSITIONS_PER_DEVICE_LIMIT = int(
    os.environ.get("GPS_POSITIONS_PER_DEVICE_LIMIT", "50000")
)

# ---- Online deb hisoblash oralig'i (sekund) ----
GPS_ONLINE_WINDOW_S = int(os.environ.get("GPS_ONLINE_WINDOW_S", "600"))

# ---- Geofence (zona nazorati) ----
# Zonadan chiqish alertlari orasidagi min interval (spam oldini olish)
GEOFENCE_ALERT_COOLDOWN_S = int(os.environ.get("GEOFENCE_ALERT_COOLDOWN_S", "300"))
# Telegram'ga yuborish (bot sozlangan bo'lsa) — 0 = faqat DB (gps_alerts)
GEOFENCE_TELEGRAM = os.environ.get("GEOFENCE_TELEGRAM", "1") in ("1", "true", "yes")

# ---- RTSP → HLS relay (ffmpeg) ----
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
HLS_DIR = os.environ.get(
    "GPS_HLS_DIR",
    os.path.join(os.environ.get("TEMP", tempfile.gettempdir()), "bm_gps_hls"),
)
HLS_RELAY_ENABLED = os.environ.get("GPS_HLS_RELAY", "1") in ("1", "true", "yes")
HLS_RELAY_CHECK_S = int(os.environ.get("GPS_HLS_RELAY_CHECK_S", "30"))

# ---- Kunlik rollup (gps_positions → gps_daily) ----
ROLLUP_ENABLED = os.environ.get("GPS_ROLLUP_ENABLED", "1") in ("1", "true", "yes")
ROLLUP_CHECK_S = int(os.environ.get("GPS_ROLLUP_CHECK_S", "1800"))
ROLLUP_DAYS = int(os.environ.get("GPS_ROLLUP_DAYS", "3"))

# ---- Tezlik nazorati ----
# 0 = o'chirilgan; aks holda km/h chegara (masalan 80)
SPEED_LIMIT_KMH = int(os.environ.get("GPS_SPEED_LIMIT_KMH", "0"))
SPEED_ALERT_COOLDOWN_S = int(os.environ.get("GPS_SPEED_ALERT_COOLDOWN_S", "600"))
SPEED_TELEGRAM = os.environ.get("GPS_SPEED_TELEGRAM", "1") in ("1", "true", "yes")

# ---- Kunlik Telegram hisoboti ----
REPORT_ENABLED = os.environ.get("GPS_REPORT_ENABLED", "1") in ("1", "true", "yes")
REPORT_HOUR = int(os.environ.get("GPS_REPORT_HOUR", "7"))  # Toshkent vaqti (0-23)
