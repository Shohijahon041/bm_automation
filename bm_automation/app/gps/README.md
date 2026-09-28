# GPS Kuzatuv — Teltonika FMB920

Bu modul avtobuslardagi Teltonika FMB920 GPS qurilmalaridan AVL ma'lumotlarini
qabul qiladi va DB'ga yozadi. **Mavjud loyihaga ta'sir qilmaydi** — faqat o'z
jadvallarini (`gps_devices`, `gps_positions`, `video_cameras`) yaratadi,
mavjud jadvallardan faqat o'qiydi.

## Ishga tushirish

```bash
python -m bm_automation.app.gps
```

Standart portlar:
- **TCP 5027** — Teltonika qurilmalar ulanadigan port
- **HTTP 8081** — kuzatuv UI + API (`/api/gps/*`, `/api/video/*`)

> **Avtomatik ishga tushirish:** standart rejimda GPS servisi dashboard bilan
> bir jarayonda fonda ko'tariladi (`GPS_AUTOSTART=1`) — alohida oyna ochish
> shart emas. Dashboard to'xtatilsa, GPS ham to'xtaydi.
> GPS servisning o'z sahifasida (`http://localhost:8081/?token=<DASHBOARD_TOKEN>`)
> va eski dashboardning ichida (`http://localhost:8080/#gps` va `#video`) —
> sidebar'da "GPS Kuzatuv" / "Video Kuzatuv" yozuvlari qo'shildi. Dashboard
> sahifasi 8081 portni iframe orqali ko'rsatadi, shuning uchun GPS servis
> ishlab turishi shart.

## FMB920 qurilmasini sozlash

SMS orqali (bir marta) — server IP'sini o'zingiznikiga almashtiring:

```
setgprps 10,SERVER_IP,5027,0,TCP,1,,,codec8e
```

Qurilma ulanganda `GPS_AUTO_REGISTER=1` (standart) bo'lsa avtomatik
ro'yxatga olinadi. So'ng frontend'da avtobusga bog'lanadi.

## Konfiguratsiya (env, ixtiyoriy)

| Var | Standart | Ma'no |
|---|---|---|
| `GPS_TCP_HOST` | `0.0.0.0` | TCP listener manzili |
| `GPS_TCP_PORT` | `5027` | Teltonika port |
| `GPS_API_HOST` | `0.0.0.0` | HTTP API manzili (tarmoqdan ham ochiq) |
| `GPS_API_PORT` | `8081` | HTTP API port |
| `GPS_AUTOSTART` | `1` | dashboard bilan bir jarayonda avtomatik ishga tushadi (`0` = faqat alohida `python -m bm_automation.app.gps`) |
| `GPS_AUTO_REGISTER` | `1` | yangi IMEI avtomatik qo'shiladi |
| `GPS_IMEI_WHITELIST` | (bo'sh) | faqat shu IMEI'lar qabul qilinadi |
| `GPS_RETENTION_DAYS` | `30` | pozitsiyalar necha kun saqlanadi |
| `GPS_ONLINE_WINDOW_S` | `600` | necha soniyada "online" hisoblanadi |
| `GEOFENCE_ALERT_COOLDOWN_S` | `300` | bir zona uchun alertlar orasidagi min interval |
| `GEOFENCE_TELEGRAM` | `1` | zonadan chiqish alertini Telegram'ga yuborish |
| `GPS_HLS_RELAY` | `1` | RTSP kameralar uchun avtomatik ffmpeg relay |
| `FFMPEG_BIN` | `ffmpeg` | ffmpeg yo'li (PATH'da bo'lsa yetarli) |
| `GPS_HLS_DIR` | `%TEMP%/bm_gps_hls` | relay HLS chiqish papkasi |
| `GPS_ROLLUP_ENABLED` | `1` | kunlik yig'ama (gps_daily) avtomatik hisoblanadi |
| `GPS_ROLLUP_CHECK_S` | `1800` | rollup intervali |
| `GPS_ROLLUP_DAYS` | `3` | necha kun qayta hisoblanadi |
| `GPS_SPEED_LIMIT_KMH` | `0` | tezlik chegarasi km/h (**0 = o'chirilgan**, masalan `80`) |
| `GPS_SPEED_ALERT_COOLDOWN_S` | `600` | bir qurilma uchun tezlik alertlari oralig'i |
| `GPS_SPEED_TELEGRAM` | `1` | tezlik alertini Telegram'ga yuborish |
| `GPS_REPORT_ENABLED` | `1` | ertalabki kunlik masofa hisoboti |
| `GPS_REPORT_HOUR` | `7` | hisobot soati (Toshkent vaqti, 0–23) |

Token: dashboard bilan bir xil `DASHBOARD_TOKEN` ishlatiladi.

## Test (simulyator)

```bash
# 1-terminal: servis
python -m bm_automation.app.gps

# 2-terminal: soxta avtobus (Toshkent bo'ylab aylanadi)
python -m bm_automation.app.gps.simulator --imei 350424106661230 --count 60
```

Frontend: `http://localhost:5173/gps`

## Video kuzatuv

Kameralar `video_cameras` jadvalida saqlanadi. Uch turdagi manba:

- `hls` — `.m3u8` stream (hls.js bilan o'ynatiladi)
- `embed` — vendor web-sahifasi (iframe)
- `rtsp` — **avtomatik relay**: servis o'zi ffmpeg ishga tushirib
  `rtsp://…` ni `http://<host>:8081/hls/cam<id>.m3u8` ga aylantiradi;
  relay qulasa monitor avtomatik qayta ko'tariladi,
  kamera o'chirilganda relay ham to'xtatiladi

### RTSP → HLS relay (qo'lda, ixtiyoriy)

`GPS_HLS_RELAY=0` bo'lsa qo'lda ko'tarish mumkin:

```bash
# Har bir kamera uchun (portlar har xil bo'lishi kerak)
ffmpeg -rtsp_transport tcp -i "rtsp://user:pass@CAMERA_IP/stream" \
  -c copy -f hls -hls_time 2 -hls_list_size 6 \
  -hls_flags delete_segments /var/www/hls/camera1.m3u8
```

So'ng `http://SERVER/hls/camera1.m3u8` ni `hls` turidagi kamera sifatida
qo'shing.

## Geofence (zona nazorati)

UI'da xarita markazi bilan zona qo'shiladi (nomi, lat/lng, radius,
ixtiyoriy avtobus). Qurilma zonadan chiqsa:

1. `gps_alerts` jadvaliga yoziladi (UI'da "Ogohlantirishlar" panelda)
2. `GEOFENCE_TELEGRAM=1` bo'lsa bot sozlangan chatga xabar yuboradi
3. bir zona uchun spamdan himoya: `GEOFENCE_ALERT_COOLDOWN_S`

## Kunlik yig'ama (rollup)

`gps_positions` har 30 daqiqada `gps_daily`ga yig'amlanadi: kunlik
masofa (haversine), maks/ortacha tezlik, nuqtalar, harakat vaqti.
Rollup qilingan kunlar pozitsiya retentionidan keyin ham qoladi —
tarix arzon saqlanadi. API: `GET /api/gps/daily?from=&to=&imei=`.

## Tezlik nazorati

`GPS_SPEED_LIMIT_KMH` (standart 0 = o'chirilgan) belgilansa —
qurilma shu tezlikdan oshsa `overspeed` alerti yoziladi va
Telegram'ga yuboriladi (bir qurilma uchun 10 daqiqalik cooldown).

## Ertalabki kunlik hisobot

Har kuni `GPS_REPORT_HOUR` (standart 07:00 Toshkent) da kechagi kun
yig'amalari Telegram'ga yuboriladi: qurilma/avtobus, km, maks tezlik.
Bot sozlanmagan bo'lsa hisobot faqat log'da qoladi.

## Xaritada qidiruv

"Jonli xarita" sarlavhasida qidiruv maydoni: avtobus raqami, ism
yoki IMEI bo'yicha; tanlanganda xarita o'sha nuqtaga uchib boradi
va popup ochiladi.

## API

Himoya: `Authorization: Bearer <DASHBOARD_TOKEN>`

| Usul | Endpoint | Ma'no |
|---|---|---|
| GET | `/api/gps/devices` | qurilmalar + oxirgi holat |
| GET | `/api/gps/positions?imei=&from=&to=&limit=` | traektoriya |
| POST | `/api/gps/devices` | `{imei, vehicle_id?, name?}` — qo'shish/bog'lash |
| PATCH | `/api/gps/devices/{imei}` | `{vehicle_id?, name?, enabled?}` |
| GET | `/api/gps/geofences` | zonalar ro'yxati |
| POST | `/api/gps/geofences` | `{name, lat, lng, radius_m?, vehicle_id?}` |
| PATCH | `/api/gps/geofences/{id}` | `{enabled?}` |
| DELETE | `/api/gps/geofences/{id}` | zonani o'chirish |
| GET | `/api/gps/alerts?limit=&imei=` | oxirgi ogohlantirishlar |
| GET | `/api/gps/daily?from=&to=&imei=` | kunlik yig'ama (rollup) |
| GET | `/api/video/cameras` | kameralar |
| GET | `/api/video/relay` | relay holati (ffmpeg, jarayonlar) |
| POST | `/api/video/cameras` | `{name, url, source_type, vehicle_id?}` |
| DELETE | `/api/video/cameras/{id}` | o'chirish |
| GET | `/api/gps/health` | servis holati (token'siz) |
