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

# ---- JT808 / JT1078 (CMSV6) 4G kameralar ----
# 4G kamera O'ZI shu portlarga ulanadi: signaling (JT808) + media (JT1078).
JT808_ENABLED = os.environ.get("JT808_ENABLED", "1") in ("1", "true", "yes")
JT808_TCP_PORT = int(os.environ.get("JT808_TCP_PORT", "6808"))
JT1078_TCP_PORT = int(os.environ.get("JT1078_TCP_PORT", "6107"))
# Qurilma media oqimini shu hostga yuboradi (VDS public IP bo'lishi kerak)
JT1078_MEDIA_HOST = os.environ.get("JT1078_MEDIA_HOST", "")

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

# ---- Yo'nalish yo'lagi (marshrutdan chiqish) ----
# Yo'lakdan shu metrdan uzoqlashsa ogohlantirish (standart)
ROUTE_DEVIATION_TOLERANCE_M = int(
    os.environ.get("GPS_ROUTE_DEVIATION_M", "200")
)
# Bir qurilma/yo'nalish uchun alertlar orasidagi min interval
ROUTE_DEVIATION_COOLDOWN_S = int(
    os.environ.get("GPS_ROUTE_DEVIATION_COOLDOWN_S", "600")
)
ROUTE_DEVIATION_TELEGRAM = os.environ.get(
    "GPS_ROUTE_DEVIATION_TELEGRAM", "1"
) in ("1", "true", "yes")
# Yo'lak o'rganishda soddalashtirish toleransi (metr)
ROUTE_LEARN_SIMPLIFY_M = int(os.environ.get("GPS_ROUTE_LEARN_SIMPLIFY_M", "40"))


# ---- Kamera GPS ↔ Teltonika GPS solishtirmasi (yordamchi) ----
# Kamera joyi Teltonika joyidan shu metrdan uzoq bo'lsa "drift" deb
# belgilanadi (o'rnatish/bog'lash xatosi bo'lishi mumkin). Bundan tashqari
# bog'lanmagan kamera shu radius ichida avtobus topilsa bog'lash tavsiya
# etiladi. Kamera GPS xaritaga/kuzatuvga chiqarilmaydi.
GPS_CAMERA_MATCH_M = int(os.environ.get("GPS_CAMERA_MATCH_M", "300"))
# 4G kamera kanallar soni (UI kanal tugmalari uchun standart).
# Ko'p kanalli qurilmalar uchun env orqali oshiriladi.
JT_CAMERA_CHANNELS = max(1, min(16, int(os.environ.get(
    "JT_CAMERA_CHANNELS", "4"))))


# ---- JT1078 xom oqim diagnostikasi ----
# 0 (standart) = serverga video SAQLANMAYDI (faqat vaqtinchalik HLS segmentlar).
# 1 = xom H.264 va raw dump diskka yoziladi (faqat diagnostika uchun).
JT1078_RAW_DUMP = os.environ.get(
    "GPS_JT1078_RAW_DUMP", "0"
) in ("1", "true", "yes")
# Yozuv (playback) so'ralganda oqim shu sekund davomida playback deb belgilanadi
JT1078_PLAYBACK_WINDOW_S = int(
    os.environ.get("GPS_JT1078_PLAYBACK_WINDOW_S", "180")
)
# Kamera JT1078 oqimida B-frame bor va aniq fps yo'q. `-use_wallclock` B-frame
# reorder bilan to'qnashib jonli oqimda tiqilib qoladi; `-c copy` esa mpegts'ga
# PTS bermaydi. Shuning uchun kirish fps'ni o'zimiz beramiz va libx264'ga
# qayta kodlab, CFR chiqishda barqaror 2s HLS olamiz.
# MUHIM: kirish fps real kadr kelish tezligiga MOS bo'lishi kerak. Aks holda
# (masalan IN=1 bo'lsa-yu kamera 3fps yuborsa) ffmpeg har kadrni 1s oraliqda
# deb belgilab, videoni 3 barobar sekinlashtiradi ("slideshow" effekti).
# 2026-10-14 da real qurilmadan o'lchandi: jonli (0x9101) ~3 fps,
# yozuv/playback (0x9201) ~13 fps. Shu sababli live va playback uchun alohida.
# Qiymatlar env orqali joyida sozlanadi (kod o'zgartirmasdan).
JT1078_FPS_IN = os.environ.get("GPS_JT1078_FPS_IN", "3")
JT1078_FPS_OUT = os.environ.get("GPS_JT1078_FPS_OUT", "6")
JT1078_FPS_IN_PB = os.environ.get("GPS_JT1078_FPS_IN_PB", "13")
JT1078_FPS_OUT_PB = os.environ.get("GPS_JT1078_FPS_OUT_PB", "13")
# ffmpeg stdin'iga yozuvchi navbat hajmi (access-unit soni). ffmpeg ulgurmasa
# (kuchsiz VPS) navbat to'ladi va eng eski kadr tashlanadi — event loop
# bloklanmaydi (ilgari bloklovchi yozuv butun serverni muzlatardi).
# Playback ~13 fps: 64 AU ≈ ~5s bufer — qisqa CPU cho'qqilarini yutadi.
JT1078_QUEUE_MAX = int(os.environ.get("GPS_JT1078_QUEUE_MAX", "64"))
# ffmpeg kirish (demuxer) navbati. Standart 8 juda kichik — ffmpeg "Thread
# message queue blocking" beradi va kirish tiqilib, kadr tashlanadi (ayniqsa
# 1 vCPU VPS'da ffmpeg CPU uchun kurashganda). Kattaroq qiymat cho'qqilarni
# yutadi. Xato: "consider raising the thread_queue_size option (current: 8)".
JT1078_THREAD_QUEUE = int(os.environ.get("GPS_JT1078_THREAD_QUEUE", "1024"))

# ---- RTSP → HLS relay (ffmpeg) ----
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
HLS_DIR = os.environ.get(
    "GPS_HLS_DIR",
    os.path.join(os.environ.get("TEMP", tempfile.gettempdir()), "bm_gps_hls"),
)
HLS_RELAY_ENABLED = os.environ.get("GPS_HLS_RELAY", "1") in ("1", "true", "yes")
HLS_RELAY_CHECK_S = int(os.environ.get("GPS_HLS_RELAY_CHECK_S", "30"))
# Bir vaqtda ishlay oladigan ffmpeg relay soni (100+ kamera uchun muhim)
HLS_RELAY_MAX = int(os.environ.get("GPS_HLS_RELAY_MAX", "8"))
# Ko'ruvchi bo'lmasa shu sekunddan keyin relay to'xtatiladi (on-demand)
HLS_RELAY_IDLE_S = int(os.environ.get("GPS_HLS_RELAY_IDLE_S", "90"))
# HLS segmentlarni shu sekunddan eski bo'lsa tozalash (server to'lmasin)
HLS_MAX_AGE_S = int(os.environ.get("GPS_HLS_MAX_AGE_S", "3600"))

# ---- Kunlik rollup (gps_positions → gps_daily) ----
ROLLUP_ENABLED = os.environ.get("GPS_ROLLUP_ENABLED", "1") in ("1", "true", "yes")
ROLLUP_CHECK_S = int(os.environ.get("GPS_ROLLUP_CHECK_S", "1800"))
ROLLUP_DAYS = int(os.environ.get("GPS_ROLLUP_DAYS", "3"))

# ---- Tezlik nazorati ----
# 0 = o'chirilgan; aks holda km/h chegara (masalan 80)
SPEED_LIMIT_KMH = int(os.environ.get("GPS_SPEED_LIMIT_KMH", "0"))
SPEED_ALERT_COOLDOWN_S = int(os.environ.get("GPS_SPEED_ALERT_COOLDOWN_S", "600"))
SPEED_TELEGRAM = os.environ.get("GPS_SPEED_TELEGRAM", "1") in ("1", "true", "yes")

# ---- To'xtashlarni aniqlash ----
STOPS_MIN_DURATION_S = int(os.environ.get("GPS_STOPS_MIN_S", "120"))
STOPS_RADIUS_M = int(os.environ.get("GPS_STOPS_RADIUS_M", "120"))

# ---- Bekorchi ishlash (idling): dvigatel yoniq, lekin harakat yo'q ----
# Shu sekunddan uzoq turgan bo'lsa ogohlantirish
IDLE_MIN_S = int(os.environ.get("GPS_IDLE_MIN_S", "600"))
IDLE_TELEGRAM = os.environ.get("GPS_IDLE_TELEGRAM", "1") in ("1", "true", "yes")
# Harakat yo'q deb hisoblash uchun maksimal tezlik (km/h)
IDLE_MAX_SPEED_KMH = int(os.environ.get("GPS_IDLE_MAX_SPEED_KMH", "0"))

# ---- Aloqa uzilishi (offline): qurilma shu sekunddan beri yubormadi ----
OFFLINE_ALERT_ENABLED = os.environ.get(
    "GPS_OFFLINE_ALERT_ENABLED", "1"
) in ("1", "true", "yes")
OFFLINE_AFTER_S = int(os.environ.get("GPS_OFFLINE_AFTER_S", "1800"))
OFFLINE_CHECK_S = int(os.environ.get("GPS_OFFLINE_CHECK_S", "300"))
# Shu sekunddan uzoq o'chiq turgan qurilmalar "eski" hisoblanadi va
# ogohlantirilmaydi (o'lik/olib tashlangan qurilmalar shovqin qilmasin)
OFFLINE_MAX_AGE_S = int(os.environ.get("GPS_OFFLINE_MAX_AGE_S", "86400"))
OFFLINE_TELEGRAM = os.environ.get(
    "GPS_OFFLINE_TELEGRAM", "1"
) in ("1", "true", "yes")

# ---- GPS km ↔ qo'lda kiritilgan km nazorati ----
# Farq shu foizdan oshsa hisobotda/nazoratda belgilanadi
VS_MANUAL_TOLERANCE_PCT = int(os.environ.get("GPS_VS_MANUAL_TOLERANCE_PCT", "20"))
# Kunlik Telegram hisobotiga nazorat bloki qo'shiladi (0 = o'chirilgan)
VS_MANUAL_REPORT = os.environ.get("GPS_VS_MANUAL_REPORT", "1") in ("1", "true", "yes")

# ---- Kunlik Telegram hisoboti ----
REPORT_ENABLED = os.environ.get("GPS_REPORT_ENABLED", "1") in ("1", "true", "yes")
REPORT_HOUR = int(os.environ.get("GPS_REPORT_HOUR", "7"))  # Toshkent vaqti (0-23)
