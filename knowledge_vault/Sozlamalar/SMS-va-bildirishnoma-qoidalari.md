# SMS va bildirishnoma qoidalari

> Telegram/SMS yuborishda yagona qoida. Maqsad — haydovchini bezovta
> qilmaslik va bir xil xabarni takror yubormaslik.

## 1. Kuniga bitta SMS

- Haydovchiga kuniga **1 ta** SMS yuboriladi.
- Takror yubormaslik uchun `sms_log` tekshiriladi.
- Xato bo'lsa ham cheksiz retry yo'q — keyingi rejim/qo'lda urinish.

## 2. Haydovchi almashsa — 2 ta

Bitta avtobusda kun davomida boshqa haydovchi chiqsa — **2 ta** SMS ruxsat:

1. Asosiy haydovchiga (smena boshida).
2. Almashtirgan haydovchiga (almashishda).

## 3. Boshqa qoidalar

- SMS faqat kunlik grafik yuborishda va alohida so'rov bo'yicha.
- Bildirishnoma rozi bo'lmaganlarga SMS yuborilmaydi
  (`notification_enabled`).
- Hujjat muddati haqida ogohlantirish ham kuniga bir marta.
- Grafik yuborish korxona bo'yicha **kuniga 20 ta** bilan cheklangan
  (`state/grafik_limit.json`, 00:00 da yangilanadi).
- PNG fayl atomik yoziladi — "file must be non-empty" xatosi bo'lmasin.

## 4. Guruhdagi chiqish/obed eslatmasi

- Har bir guruh o'zi uchun `/chiqish_on` yoki `/chiqish_off` bilan yoqadi.
- Vaqt `departure_offset_min` bilan suriladi: **-180..+180 daqiqa**
  (manfiy — oldin, musbat — keyin).
- Bu agent `AI_AUTO_AGENTS`'ga **bog'liq emas**; global o'chirish —
  `GROUP_DEPARTURE_NOTIFY=off`.
- Holat `state/group_departures.json`da saqlanadi.

## 5. Avtomatik hisobotlar

| Xabar | Qachon | Sozlama |
|---|---|---|
| Kunlik xulosa | har kuni (standart 08:00) | `AI_DAILY_SUMMARY`, `AI_DAILY_SUMMARY_HOUR` |
| Self-review | har kuni (standart 09:00) | `AI_SELFREVIEW`, `AI_SELFREVIEW_HOUR` |
| Muammoli reyslar | interval bilan | `AI_AUTO_AGENTS` |
| Hujjat muddati | interval bilan | `AI_AUTO_AGENTS` |
| Oylik natijalar | oy yakunida | `AI_AUTO_AGENTS` |
| Jarimalar | interval bilan | `FINES_REPORT` |

> Default `AI_AUTO_AGENTS=off` — bot faqat buyruqlarga javob beradi.
> Yoqilganda agentlar o'z jadvalida ishlaydi.

## 6. Oylik va takror yuborish

- Oylik Excel bir marta yuboriladi; retry'lar dedup orqali bloklanadi
  (`state/daily_monthly_sent.json` — [[oylik-takror]]).
- Dashboardda oylik har doim ko'rinadi.
- Botda haydovchi o'z oyligini faqat `bot_driver_salary` yoqilganda ko'radi;
  eski `show_driver_salary` kaliti e'tiborsiz.

## 7. Jim rejim

- `AI_AUTO_AGENTS=off` bo'lsa: rejalashtirilgan xabarlar yo'q.
- Guruh eslatmasi va avtomatik sync bundan mustasno — o'z holatiga qarab
  ishlaydi.
- Har qanday avtomatik yuborishdan oldin "bugun yuborilganmi?" tekshiriladi.

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Ish-oqimlari]]
- [[Sinxronizatsiya-oqimi]]
- [[oylik-takror]]
- [[Muhit-va-konfiguratsiya]]

<!-- avto-bog'liq:end -->
