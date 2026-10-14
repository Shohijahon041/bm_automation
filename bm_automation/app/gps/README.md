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
| `GPS_IDLE_MIN_S` | `600` | bekorchi ishlash (idling) minimal davomiyligi (sekund) |
| `GPS_IDLE_TELEGRAM` | `1` | idling alertini Telegram'ga yuborish |
| `GPS_IDLE_MAX_SPEED_KMH` | `0` | "harakat yo'q" deb hisoblash chegarasi (km/h) |
| `GPS_OFFLINE_ALERT_ENABLED` | `1` | aloqa uzilishi ogohlantirishi yoqilgan |
| `GPS_OFFLINE_AFTER_S` | `1800` | shu sekunddan beri yubormasa offline |
| `GPS_OFFLINE_CHECK_S` | `300` | offline tekshiruv intervali (sekund) |
| `GPS_OFFLINE_MAX_AGE_S` | `86400` | shundan uzoq o'chiq turganlar ogohlantirilmaydi |
| `GPS_OFFLINE_TELEGRAM` | `1` | offline alertini Telegram'ga yuborish |
| `JT808_ENABLED` | `1` | JT808/JT1078 4G kamera listenerlarini yoqish |
| `JT808_TCP_PORT` | `6808` | JT808 signaling porti |
| `JT1078_TCP_PORT` | `6107` | JT1078 media porti |
| `JT1078_MEDIA_HOST` | (bo'sh) | qurilma oqim yuboradigan public IP/host |
| `GPS_JT1078_RAW_DUMP` | `0` | `1` = xom video diskka yoziladi (faqat diagnostika) |
| `GPS_JT1078_PLAYBACK_WINDOW_S` | `180` | playback rejimi oqimi shu sekund davomida belgilanadi |
| `GPS_HLS_MAX_AGE_S` | `3600` | shu sekunddan eski HLS segmentlar tozalanadi |

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

### Avtobus bo'yicha (video kuzatuv xonasi)

Har bir kamera (HLS/RTSP) va har bir 4G kamera terminali (JT808/JT1078)
avtobusga bog'lanadi: HLS/RTSP → `video_cameras.vehicle_id`, 4G terminal →
`gps_devices.vehicle_id` (kind=`camera`). Video bo'limining yuqorisida
**🚌 Avtobuslar** chiplari bor — bir nechtasini (yoki hammasini) tanlab,
faqat shu avtobuslarning kameralarini ko'rish mumkin. Tanlov bo'sh bo'lsa
"Barchasi" ko'rsatiladi.

Kanal bo'yicha: 4G kamera kartasida har bir kanal alohida tugma
(`▶ ch1 … chN`) va **▶ Barcha kanallar** tugmasi bor. Kanal tugmasi o'sha
kanalga `0x9101` yuboradi, jonli oqim kartasi sarlavhasida
`📷 <IMEI/SIM> · kanal N` ko'rinadi.

**Xaritadan videoga:** jonli xaritada avtobusni (marker yoki ro'yxatdagi
qatorni) tanlaganda uning kartasida **🎥 Video** tugmasi chiqadi — u video
bo'limini ochib, shu avtobusni tanlaydi va kameralarini (HLS/RTSP + 4G
kanallarini) avtomatik ochadi.

**Kamera GPS — faqat yordamchi.** 4G kamera o'z GPS joyini ham yuboradi,
lekin u **xaritaga, trekka va nazoratga chiqarilmaydi** (`gps_devices.kind`
= `camera`; `/api/gps/devices`, offline/tezlik/yo'lak/idling va kunlik
hisobot faqat Teltonika'ni hisobga oladi). Kamera joyi
`/api/gps/camera-assist` va `jt-streams` javoblarida **solishtirma** uchun
ishlatiladi:

- `ok` — kamera GPS Teltonika GPS bilan mos (farq < `GPS_CAMERA_MATCH_M`);
- `drift` — farq katta: kamera boshqa avtobusda yoki GPS chalg'iyapti;
- `suggest` — bog'lanmagan kamera yaqin avtobus topildi (bog'lash tavsiyasi,
  UI'da bir bosishda bog'lanadi);
- `unbound` / `no_camera_gps` / `no_teltonika`.

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

## JT808 / JT1078 — 4G (CMSV6) kameralar

4G avtosko'zgular/simlar **JT/T 808** (signaling) va **JT1078** (media)
protokollarida ishlaydi. Ular to'g'ridan-to'g'ri serverga ulanadi — ochiq
portlar:

- **TCP 6808** — JT808 signaling (ro'yxat, heartbeat, buyruqlar)
- **TCP 6107** — JT1078 media (video paketlari)

**Portni adashtirish muhim emas:** ikkala listener birinchi paketga qarab
protokolni O'ZI aniqlaydi (JT808 — `0x7e…`, media — `30316364`), ya'ni
qurilmada faqat bitta "server port" maydoni bo'lib u 6107 (yoki aksincha,
6808) qilib qo'yilgan bo'lsa ham signaling va video to'g'ri ishlanadi.
Baribir standart sozlama tavsiya etiladi: platforma (JT808) **6808**, media
**6107**. Har bir yangi ulanishning birinchi baytlari logga yoziladi
(`... ulanish <peer> — birinchi N bayt: <hex>`) — qurilma qaysi protokolda
gapiryapti yoki umuman javob bermayaptimi, shu qatordan ko'rinadi.

**Qaysi kamera/kanal?** IMEI/kanal faqat birinchi paketdan ma'lum bo'ladi,
shuning uchun har bir log qatoriga manba `ip:port` bilan birga
`<IMEI>_<kanal>` qo'shiladi (bir ulanish bir necha kanal yuborsa `/` bilan):

```
JT1078: yangi ulanish 185.163.26.9:52763 221080344988_1/221080344988_3
JT1078: media ulanishi 185.163.26.9:52763 221080344988_1/221080344988_3
JT1078: live oqim boshlandi 221080344988_7 kodek=h264 ... manba=185.163.26.9
JT1078: media uzildi 185.163.26.9:52763 221080344988_1/221080344988_3
JT808:  yangi ulanish 185.163.26.9:52763 IMEI=221080344988
JT808:  qurilma uzildi 185.163.26.9:52763 IMEI=221080344988
```

Qurilma qisqa-qisqa qayta ulanadi va har safar **boshqa port**dan keladi —
shuning uchun IMEI **manba IP bo'yicha** eslab qolinadi: birinchi ulanishda
IMEI hali o'qilmagan bo'lsa yorliq faqat `ip:port` bo'ladi, keyingi
ulanishlarda esa darhol `IMEI`/`kanal` ko'rinadi (reyestr chegaralangan —
uzoq jarayonda xotira o'smaydi). `qurilma uzildi` sababi (masalan
`120s data kelmadi`) ham alohida qator bo'lib tushadi.

**Jonli (live):** UI'da qurilma kartasidagi kanalni tanlab "▶ Jonli video"
bosilsa server qurilmaga `0x9101` yuboradi; qurilma oqimni 6107-portga uzatadi,
ffmpeg uni HLS'ga aylantiradi (`/hls/jt_<sim>_<kanal>.m3u8`). "⏹ To'xtatish"
`0x9102` yuboradi (4G trafikni tejash).

**Yozuvlar (playback, qurilma xotirasidan):** UI'da "🗂 Xotira" → kanal
(ch1..chN), sana va vaqt oralig'i (Dan/Gacha) tanlanadi → `0x9205` bilan
ro'yxat so'raladi (`GET /api/video/jt-files`), tanlangan yozuv `0x9201` bilan
oqim sifatida so'raladi va `/hls/jtpb_<sim>_<kanal>.m3u8` orqali ko'rsatiladi.
Pleyer imkoniyatlari:

- **Timeline + seek** — slayder joriy pozitsiyani ko'rsatadi; surilsa `0x9202`
  `drag=5` bilan qurilma belgilangan vaqtga o'tadi (offset yozuv `start`idan
  hisoblanadi).
- **Tezlik** — `1x/2x/4x/8x`; `0x9202` `tez=3` + `speed` bilan, video
  `playbackRate` ham sinxronlanadi.
- **Pauza / davom / stop / tez / orqaga** — `0x9202` (Table 25).
- **Ketma-ket ijro** — yozuv tugagach keyingisiga avtomatik o'tadi.
- **Yuklab olish** — joriy oqim brauzerda (MediaRecorder + `captureStream`)
  `.webm` fayl qilib saqlanadi; serverga yozilmaydi.

Pleyer oqim tayyor bo'lguncha kutadi (retry/status) va joriy
pozitsiya/davomiylikni ko'rsatadi. **Video serverga saqlanmaydi** —
qurilmaning o'z xotirasida qoladi, server faqat jonli uzatish (HLS) qiladi.

> 0x9202 boshqaruv (JT/T 1078-2016 Table 25): play=0, pauza=1, stop=2,
> tez=3, orqaga=4, drag=5 (vaqtga o'tish), kalit=6.
>
> 0x9201 tanasi (JT/T 1078-2016 Table 24) da `10+n` "tez oldinga/orqaga
> koeffitsiyenti" bayti MAJBURIY (0) — usiz qurilma start/end vaqtni bir
> baytga siljigan holda o'qib, `0x1205 total=0` qaytaradi.

**Ovoz:** qurilma audio paketlarini (G.711A/U) yuborsa ham HLS'ga
qo'shilmaydi — faqat video uzatiladi (audio paketlar tashlanadi).

`JT1078_MEDIA_HOST` — qurilma oqimni yuboradigan public IP/host (bo'sh bo'lsa
UI'dagi host ishlatiladi). `GPS_JT1078_RAW_DUMP=1` faqat diagnostika uchun
xom oqimni diskka yozadi (standart `0` = saqlanmaydi). Bo'sh/eskirgan HLS
segmentlar `GPS_HLS_MAX_AGE_S` bo'yicha avtomatik tozalanadi.

### Oqim diagnostikasi (dashboard)

Har bir kanal kartasi ostida alomatlar qatori ko'rinadi va u 5 sekundda
bir yangilanadi (`GET /api/video/jt-streams`):

- **kodek** — `H.264` / `H.265` (PT 98/99 bo'yicha; sarlavhadagi `H.265`
  badge ham shu qiymatdan olinadi)
- **⏱ kalit kadr** — oxirgi I-kadr muxer'ga qancha vaqt oldin yozilgani
  (20 sekunddan oshsa sariq ogohlantirish). Kalit kadr umuman bo'lmasa
  `kalit kadr yo'q` — odatda qurilma P-kadr yuboryapti yoki oqim uzilgan
- **kadr / kalit** — muxer'ga yozilgan access-unit va I-kadrlar soni
- **↻ N marta qayta** — muxer qayta ishga tushirilgan holatlar: kodek
  almashishi, ffmpeg o'lishi
- **⚠ oxirgi xato** — ilova xatosi (masalan `ffmpeg yozish xatosi — qayta
  ishga tushiriladi`) va/yoki ffmpeg'ning oxirgi ma'noli stderr qatori
  (`ffmpeg: [h264 @ ...] no frame!` kabi). Doimiy, ahamiyatsiz
  ogohlantirishlar (`non monotonically increasing dts`, `-vsync is
  deprecated`) filtrlanadi (`app/gps/diag.py`)

Oqim yopilganda (kamera uzildi / muxer qayta yaratildi / ffmpeg xatosi)
yozuvi darhol yo'qolmaydi: UI'da **"Yaqinda yopilgan oqimlar"** panelida
sababi va oxirgi xatosi bilan ko'rinadi (`recent` maydoni, 24 soat saqlanadi,
`app/gps/jt1078.py:_DIAG`). Bu "video nega o'chdi?" savoliga javob beradi.

RTSP kameralar uchun ham xuddi shunday: relay kartasi ostida
`▶ ffmpeg ishlayapti` / `⏹ ffmpeg to'xtagan (kod N)`, qayta ishga tushish
soni va ffmpeg'ning oxirgi xatosi (stderr → `<HLS_DIR>/relay<id>.log`)
ko'rinadi.

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

## Bekorchi ishlash (idling)

Qurilma dvigateli yoniq, lekin harakat qilmay (`GPS_IDLE_MAX_SPEED_KMH`dan
sekin) `GPS_IDLE_MIN_S` (standart 10 daqiqa) davomida tursa — `idling`
alerti yoziladi va Telegram'ga yuboriladi. Har bir epizod uchun bir marta;
harakat boshlansa yoki dvigatel o'chsa epizod yopiladi.

## Aloqa uzilishi (offline)

Fon tredi (`GPS_OFFLINE_CHECK_S`, standart 5 daqiqa) har bir yoqilgan
qurilmaning oxirgi aloqa vaqtini tekshiradi. `GPS_OFFLINE_AFTER_S`
(standart 30 daqiqa) dan beri yubormagan bo'lsa — `offline` alerti
yoziladi va Telegram'ga yuboriladi. Bir uzilish davrida bir marta; qurilma
qayta yubora boshlasa holat "online"ga o'zgaradi.

## Ertalabki kunlik hisobot

Har kuni `GPS_REPORT_HOUR` (standart 07:00 Toshkent) da kechagi kun
yig'amalari Telegram'ga yuboriladi: qurilma/avtobus, km, maks tezlik.
Bot sozlanmagan bo'lsa hisobot faqat log'da qoladi.

## Xaritada qidiruv

"Jonli xarita" sarlavhasida qidiruv maydoni: avtobus raqami, ism
yoki IMEI bo'yicha; tanlanganda xarita o'sha nuqtaga uchib boradi
va popup ochiladi.

## Traektoriya (kengaytirilgan)

- **Vaqt oralig'i**: boshlanish/tugash `datetime-local` bilan (soat aniqligida, Toshkent +05:00).
- **Rangli chiziq**: tezlikka qarab segmentlar — ≤40 yashil, 41–70 sariq, ≥71 qizil.
- **To'xtashlar**: bir joyda ≥2 daqiqa turgan epizodlar sariq markerlar
  va jadval bilan (boshlanish, davomiylik, joy, dvigatel holati).
  Jadvaldagi qatorni bosish xaritada shu nuqtaga yaqinlashtiradi.
- **CSV eksport**: "⬇ CSV" — pozitsiyalar; to'xtashlar jadvalida alohida CSV.

## To'xtashlarni aniqlash (API)

`GET /api/gps/stops?imei=&from=&to=&min_s=&radius=` — bir joyda uzoq
turgan epizodlar. `imei` o'rniga `vehicle_id` yoki hech narsa
(qamrovdagi barcha qurilmalar, ko'pi bilan 20 ta) berilishi mumkin.
Nuqtalar radius ichida qolsa bitta guruh; harakat nuqtalari (tezlik
>15 km/h) guruhga kirmaydi, lekin radiusdan chiqsa guruh yopiladi.
Geofence ichida to'xtagan bo'lsa — zona nomi `fence` maydonida.

## GPS km ↔ qo'lda km nazorati

`GET /api/gps/vs-manual?from=&to=` — `gps_daily` (GPS o'lchagan kunlik
masofa) va `driver_work_logs` (haydovchi kiritgan km, `note != 'AVTO'`)
ni (avtobus, kun) bo'yicha taqqoslaydi. UI'da "Qo'lda km nazorati"
kartasi: farq % (yashil ≤10%, sariq ≤25%, qizil >25%), CSV eksport.
Kunlik Telegram hisobotiga ham nazorat bloki qo'shiladi (faqat
`GPS_VS_MANUAL_TOLERANCE_PCT`dan oshgan qatorlar):

- ⚠️ qo'lda kiritilgan km GPS'dan ancha ko'p
- 🔻 qo'lda km GPS'dan ancha kam
- ❓ qo'lda yozuv bor, GPS yozuvi yo'q (qurilma o'chiq?)
- ❗ GPS yurgan (≥20 km), qayd yo'q

Kunlik hisobotni qo'lda yuborish: UI'da "📨 Telegramga yuborish"
(POST `/api/gps/daily/send`, admin).

## Konfiguratsiya (qo'shimcha)

| Var | Standart | Ma'no |
|---|---|---|
| `GPS_CAMERA_MATCH_M` | `300` | kamera GPS Teltonika GPS bilan mos hisoblanadigan radius (m); shundan uzoq — `drift`, bog'lanmagani uchun bog'lash tavsiyasi |
| `JT_CAMERA_CHANNELS` | `4` | 4G kamera kanallari soni (UI kanal tugmalari) |
| `GPS_STOPS_MIN_S` | `120` | to'xtash minimal davomiyligi (sekund) |
| `GPS_STOPS_RADIUS_M` | `120` | to'xtash radiusi (metr) |
| `GPS_VS_MANUAL_TOLERANCE_PCT` | `20` | GPS↔qo'lda km farqi hisobotda belgilanadigan chegara (%) |
| `GPS_VS_MANUAL_REPORT` | `1` | kunlik Telegram hisobotiga nazorat bloki qo'shish (0 = o'chirilgan) |

## API

Himoya: `Authorization: Bearer <DASHBOARD_TOKEN>`

| Usul | Endpoint | Ma'no |
|---|---|---|
| GET | `/api/gps/devices` | Teltonika qurilmalari + oxirgi holat (kameralar chiqarilmaydi) |
| GET | `/api/gps/camera-assist` | kamera GPS ↔ Teltonika GPS solishtirmasi (mos/drift/bog'lash tavsiyasi) |
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
| GET | `/api/video/relay` | relay holati (ffmpeg, jarayonlar, oxirgi xato) |
| GET | `/api/video/jt-streams` | JT1078 faol oqimlar + qurilmalar + diagnostika + kamera↔avtobus bog'lanishi (`bindings`), kanal soni (`camera_channels`), GPS mosligi (`assist`) |
| POST | `/api/video/jt-live` | `{phone, channel}` — `0x9101` jonli oqim |
| POST | `/api/video/jt-stop` | `{phone, channel}` — `0x9102` to'xtatish |
| GET | `/api/video/jt-files?phone=` | qurilma xotirasidagi yozuvlar (kesh) |
| POST | `/api/video/jt-files` | `{phone, channel, date? \| start?, end?}` — `0x9205` ro'yxat so'rash |
| POST | `/api/video/jt-playback` | `{phone, channel, start, end, host?, media?, stream?, memory?, method?, multiples?}` — `0x9201` yozuv oqimi |
| POST | `/api/video/jt-playback-control` | `{phone, channel, control, speed?, time?}` — `0x9202` (`time` = drag uchun maqsad vaqt) |
| POST | `/api/video/cameras` | `{name, url, source_type, vehicle_id?}` |
| DELETE | `/api/video/cameras/{id}` | o'chirish |
| GET | `/api/gps/health` | servis holati (token'siz) |
