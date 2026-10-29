# GPS kuzatuv (JT808 / JT1078)

> `bm_automation/app/gps/` — avtobuslardagi GPS qurilmalaridan kelgan
> ma'lumotni qabul qiladi, saqlaydi va panelda ko'rsatadi. Bu qatlam
> BM API'ga bog'liq emas — qurilmalar bilan to'g'ridan-to'g'ri ishlaydi.

## Portlar va kanallar

| Nima | Standart | Sozlama |
| --- | --- | --- |
| Umumiy TCP listener | 5027 | `GPS_TCP_PORT`, `GPS_TCP_HOST` |
| JT808 (protokol) | 6808 | `JT808_TCP_PORT`, `JT808_ENABLED` |
| JT1078 (video/media) | 6107 | `JT1078_TCP_PORT`, `JT1078_MEDIA_HOST` |
| GPS API (panel uchun) | 8081 | `GPS_API_PORT` |

API tokeni `DASHBOARD_TOKEN` dan olinadi — panel va GPS API bir xil
himoyadan foydalanadi ([[Qoidalar/Xavfsizlik]]).

## Modullar

- `listener.py` — TCP server, ulanishlarni qabul qilish.
- `jt808.py` — asosiy protokol: pozitsiya, holat, registratsiya.
- `jt1078.py` — video/media oqimi (kanallar, `JT_CAMERA_CHANNELS`).
- `codec.py`, `simulator.py` — kodlash va sinov qurilmasi.
- `storage.py` — pozitsiyalar saqlanadi; saqlash muddati
  `GPS_RETENTION_DAYS` (standart 30 kun), qurilma boshiga yozuv chegarasi
  `GPS_POSITIONS_PER_DEVICE_LIMIT`.
- `geofence.py`, `route_alert.py`, `idling.py`, `speed.py`, `stops.py`,
  `rollup.py`, `report.py` — tahlil va ogohlantirishlar.
- `compare.py` — GPS ma'lumoti bilan BM (reys/km) ma'lumotini solishtirish.
- `camera.py` — kamera kadri va GPS pozitsiyasini moslashtirish.
- `offline.py`, `autostart.py`, `diag.py` — oflayn aniqlash, avtoyuklash.

## Muhim sozlamalar

- `GPS_ONLINE_WINDOW_S` (600) — shu vaqt ichida xabar kelmagan qurilma
  oflayn hisoblanadi.
- `GEOFENCE_TELEGRAM`, `GEOFENCE_ALERT_COOLDOWN_S` (300) — hudud
  ogohlantirishlari va takrorlanmasligi (cooldown).
- `GPS_ROUTE_DEVIATION_M` (200), `GPS_ROUTE_DEVIATION_COOLDOWN_S` (600) —
  yo'nalishdan chetlashish.
- `GPS_CAMERA_MATCH_M` (300), `GPS_CAMERA_FRESH_S` (900) — kamera kadriga
  mos pozitsiya va uning "yangi"ligi.
- `GPS_AUTO_REGISTER` (1) — yangi IMEI avtomatik ro'yxatga olinadi;
  `GPS_IMEI_WHITELIST` bilan cheklash mumkin.

## Tipik xatolar

- Port band (eski jarayon qolmagan) → listener ko'tarilmaydi; avval
  jarayonni topib to'xtatish kerak.
- Qurilma vaqti bilan server vaqti farq qilsa — pozitsiyalar tartibsiz
  ko'rinadi (vaqt UTC bo'yicha saqlanadi).
- Video oqimi yoqilmagan bo'lsa `JT1078` porti jim turadi — bu xato emas.

Sinov: `tests/test_jt808.py`, `tests/test_jt1078.py`, `tests/test_gps_api.py`,
`tests/test_gps_camera.py`, `tests/test_gps_compare.py`.

Bog'liq: [[Qoidalar/Sinxronizatsiya-oqimi]] · [[Sozlamalar/Integratsiyalar]]

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Integratsiyalar]]
- [[Arxitektura]]
- [[Atamalar]]
- [[Muhit-va-konfiguratsiya]]

<!-- avto-bog'liq:end -->
