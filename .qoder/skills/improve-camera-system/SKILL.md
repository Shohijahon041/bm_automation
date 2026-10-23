---
name: improve-camera-system
description: BM dashboard GPS/Video kamera tizimini to‘liq yaxshilash: player va ko‘rinadigan tugmalar, snapshot/tasvir, JT808/JT1078 ulanishi, FLV/HLS oqimi, ZLMediaKit integratsiyasi, xavfsizlik, test, brauzer tekshiruvi va VDS deploy. Kamera ishlamayapti, video ochilmayapti, tugmalar ko‘rinmayapti, tasvir sifati yoki ulanish barqarorligi yomon, xavfsizlik yoki integratsiyani tekshirish/tuzatish so‘ralganda ishlatish.
argument-hint: <kamera muammosi yoki yaxshilash talabi>
---

# Improve Camera System

BM dashboarddagi GPS/Video kameralarini koddan ishlab turgan VDS holatigacha yaxshilash. Faqat hisobot bermasdan, sababni topish, minimal tuzatish, test, brauzer tekshiruvi va so‘ralgan to‘liq ishda deployni bajarish.

## Ishlash tartibi

1. Talabni quyidagi qatlamlarga ajratish:
   - UI: player, boshqaruv tugmalari, holat va mobil ko‘rinish.
   - Tasvir: jonli video, snapshot, fullscreen, aspect ratio va xato holati.
   - Ulanish: qurilma → JT808 signal → JT1078 media → ZLMediaKit → player.
   - Xavfsizlik: autentifikatsiya, kompaniya scope, token, HMAC va input validatsiyasi.
   - Integratsiya: GPS qurilmasi, avtobus, kanal, playback va Setka.
2. `references/camera-system.md` ni o‘qish va tegishli fayllarni tekshirish.
3. Mavjud holatni o‘zgartirishdan oldin qayta ishlab ko‘rish:
   - xizmatlar holati;
   - browser console va network;
   - qurilma online/offline holati;
   - stream start javobi va playerga kelgan media;
   - bir xil stream qayta ishga tushirilganda duplicate jarayon yo‘qligi.
4. Muammoning eng past qatlamini aniqlash. Player xatosini UI bilan yashirmaslik; qurilma, media yoki muxer xatosini o‘sha qatlamda tuzatish.
5. Faqat talabga kerakli minimal fayllarni o‘zgartirish. Mavjud Video playerni Setka uchun qayta ishlatish; parallel ikkinchi player arxitekturasini yaratmaslik.
6. Kod va xavfsizlik tekshiruvlarini bajarish.
7. Dashboardni browserda ishlatib golden path va edge case’larni tekshirish.
8. To‘liq tuzatish so‘ralgan bo‘lsa VDS’ga backup bilan deploy qilish va jonli tekshirish.

## UI va boshqaruvlar

- Asosiy amallarni doim ko‘rinadigan qilish: **Boshlash**, **To‘xtatish**, **Snapshot**, **To‘liq ekran**.
- Icon-only tugmaga `aria-label` va tooltip berish; muhim amalni faqat hover ichida yashirmaslik.
- Touch targetni kamida 44×44 px qilish va tugmalar orasida yetarli masofa saqlash.
- Tugma holatlarini ajratish: idle, loading, active, disabled, error.
- Async amal davomida takror bosishni bloklash va foydalanuvchiga holat ko‘rsatish.
- Online kameralarni yuqorida, offline qurilmalarni pastda ko‘rsatish; offline qurilmani ro‘yxatdan yo‘qotmaslik.
- Setkada mavjud player logikasini ishlatish; barcha kanallarni bir sahifada boshqarish.
- Mobil ekran, klaviatura fokusi va `prefers-reduced-motion` ni tekshirish.
- Emoji o‘rniga loyihadagi bir xil SVG/icon tizimidan foydalanish.

## Tasvir va player

- Video uchun aniq aspect ratio saqlash; tasvirni cho‘zmaslik.
- Stream tayyor bo‘lguncha loading, kelmasa sababli error va **Qayta urinish** ko‘rsatish.
- Snapshotni real faol player yoki media endpointidan olish; bo‘sh kadrni muvaffaqiyat deb ko‘rsatmaslik.
- Fullscreen’dan chiqqanda player sessiyasi va tanlangan kanalni saqlash.
- Audio autoplay cheklovlarini buzmaslik; ovozni foydalanuvchi amali bilan yoqish.
- FLV/HLS fallback ishlatilsa, bitta sessiya uchun ikki muxerni parallel qoldirmaslik.
- Streamni to‘xtatganda player, server sessiyasi va media jarayonini izchil yopish.

## Ulanish va barqarorlik

Quyidagi zanjirni bosqichma-bosqich tekshirish:

`qurilma online → kanal mavjud → 0x9101 start → JT1078 paketlari → ZLMediaKit stream → HTTP-FLV/HLS → browser player`

- Har qatlam uchun aniq status va timeout ishlatish.
- Bir streamni qayta start qilish duplicate ffmpeg/muxer yaratmasligini tekshirish.
- Stop suppression va start/stop race holatlarini test qilish.
- Qurilma offline bo‘lsa start buyrug‘ini muvaffaqiyat deb qaytarmaslik.
- Kanal raqami, device ID va stream ID ni validatsiya qilish.
- Network uzilib qaytganda cheklangan retry va tushunarli holat ishlatish; cheksiz retry yaratmaslik.
- Jarayonni faqat sintaksis/test bilan emas, real browser playback bilan tasdiqlash.

## Xavfsizlik

- `.env`, `DASHBOARD_TOKEN`, `VIDEO_TOKEN_SECRET`, qurilma credentiallari va URL tokenlarini hech qachon chiqarish, loglash yoki commit qilish mumkin emas.
- Tokenni browser navigatsiya URL’iga yoki tool natijasiga kiritmaslik. Mavjud xavfsiz session/localStorage mexanizmini qiymatni oshkor qilmasdan ishlatish.
- Kamera API’larida autentifikatsiya, kompaniya scope va video ruxsatini server tomonida majburiy tekshirish.
- UI’da tugmani yashirishni ruxsat nazorati deb hisoblamaslik.
- Device ID, kanal, sana oralig‘i, stream turi va fayl yo‘lini server chegarasida validatsiya qilish.
- Shell command tuzilsa foydalanuvchi qiymatini string biriktirish bilan qo‘shmaslik.
- HMAC token uchun muddat, maqsad va resurs scope’ini tekshirish; secretni loglamaslik.
- Rate limit va auditni start/stop/playback kabi qimmat amallarda saqlash.
- CORS yoki CSP’ni muammoni yashirish uchun kengaytirmaslik.

## Integratsiyalar

- GPS qurilma ↔ avtobus bog‘lanishini yagona source of truth orqali olish.
- Bitta avtobusga bir nechta qurilma bo‘lsa har birining turi va kanal holatini alohida ko‘rsatish.
- Video, Setka va GPS xarita sahifalari bir xil device/channel identifikatorlaridan foydalanishini tekshirish.
- Live va playback sessiyalarini aralashtirmaslik.
- ZLMediaKit API yoki stream nomlash o‘zgarsa producer va player tomonini birgalikda yangilash.
- Yangi integratsiyani mavjud auth, audit va kompaniya scope tizimiga ulash.

## Tekshiruv

Har tuzatishdan keyin kamida:

1. Tegishli Python testlarini to‘liq interpreter yo‘li va `-X utf8` bilan bajarish.
2. O‘zgargan Python modullarini `py_compile` bilan tekshirish.
3. `gps_ui.html` yoki `index.html` inline scriptlarini ajratib `node --check` bilan tekshirish.
4. Browserda quyidagilarni ishlatish:
   - bitta online kamerani boshlash va to‘xtatish;
   - snapshot va fullscreen;
   - Setkada **Barchasini boshlash**;
   - offline kamera;
   - qayta bosish va network xatosi;
   - desktop va mobil viewport;
   - console error va muvaffaqiyatsiz network so‘rovlari.
5. Xavfsizlik regressiyasini tekshirish: ruxsatsiz API, boshqa kompaniya qurilmasi va noto‘g‘ri channel/device qiymati.

UI’ni browserda ishlatib bo‘lmasa, buni aniq aytish; faqat testlar asosida “to‘liq ishlaydi” demaslik.

## VDS deploy

- Faqat foydalanuvchi to‘liq tuzatish yoki deployni so‘raganida bajarish.
- Deploydan oldin o‘zgargan fayllarning VDS nusxasini timestamp’li backup katalogiga olish.
- Server SFTP’ni qabul qilmasa `scp -O` ishlatish.
- `.env`, `state/`, token va profil credential fayllarini almashtirmaslik.
- Python fayli o‘zgarsa tegishli xizmatni restart qilish; faqat statik HTML bo‘lsa restart zarurligini arxitekturadan tekshirish.
- `bm-dashboard` to‘xtashi GPS threadlari sabab cho‘zilishi mumkin; jarayon yakunini kutish va yangi xizmat `active` bo‘lganini tekshirish.
- Deploydan keyin md5, `py_compile`, service status, journal traceback, API va browser playbackni tekshirish.
- Muammo chiqsa backupdan qaytarish; foydalanuvchi ishini yo‘qotadigan destructive command ishlatmaslik.

## Yakuniy hisobot

Qisqa va operatsion yozish:

- nima ishlamayotgan edi;
- nima tuzatildi;
- foydalanuvchi endi qaysi tugmalar va imkoniyatlardan foydalanadi;
- qaysi test va jonli sinovlar o‘tdi;
- deploy qilingan-qilinmagani;
- qolgan real qurilma yoki tashqi servis cheklovi.
