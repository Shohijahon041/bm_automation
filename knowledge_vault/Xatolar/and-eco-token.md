# AND ECO 401 — profil tokeni

> Bitta korxona (AND ECO) kunlik vazifada butunlay o'tkazib yuborilgan
> holat. Sabab — profil tokeni olinmagan, lekin asosiy token ishlagan.

## Semptom

- Kunlik `daily` hisobotida AND ECO uchun na grafik, na oylik yuboriladi.
- Logda:
  `XATO [AND ECO] token: ... HTTP 401` yoki `avtorizatsiya`.
- Boshqa korxonalar normal ishlaydi — muammo faqat bitta profilda.

## Ildiz sabab

`BMClient.login_for_profile(profileId)` — `get-token-by-profile`
endpointi orqali kompaniya tokenini oladi. AND ECO uchun bu endpoint
**401/403** qaytargan (profil eskirgan yoki ruxsat yo'q). Kod istisno
ko'tarib, butun korxonani `continue` bilan tashlab ketgan.

Muhim nuqta: **asosiy token** o'sha kompaniya ma'lumotlarini o'qiy
olardi — ya'ni ma'lumot yo'q emas, faqat token olish yo'li buzuq edi.

## Yechim

`login_for_profile(pid, fallback_to_main=True)`:

- `BMAuthError` yoki `BMApiError` (status 401/403) → ogohlantirish
  yoziladi va `load_tokens_from_file()` bilan **asosiy token** tiklanadi;
- boshqa xatolar (`429` rate-limit, `500`, tarmoq) — **ko'tarilib
  qoladi**, chunki ularni yashirish noto'g'ri bo'lardi;
- `profileId` bo'sh bo'lsa — to'g'ridan-to'g'ri asosiy token
  (`use_main()`).

`run_daily()` boshida yangi OneID login **bir marta** olinadi (retry
siklidan tashqarida) — har urinishda brauzer ochib server xotirasini
yeyishning oldini oladi.

## Qoidalar

1. Bitta korxona xatosi butun oqimni to'xtatmasin — xato `entry`ga
   yoziladi, qolganlar davom etadi.
2. Fallback faqat **autentifikatsiya** xatolarida (401/403); boshqa
   xatolarni yashirmang.
3. Fallback ishlagani logda ko'rinsin — "jim" tuzatish keyin
   diagnostikani qiyinlashtiradi.
4. Qayta urinish cheklangan bo'lsin (`max_attempts`), cheksiz emas.
5. Catch-up: deploydan keyin o'sha kun uchun `daily` qo'lda bir marta
   ishga tushiriladi.

Bog'liq: [[Ish-oqimlari]], [[Integratsiyalar]], [[oylik-takror]].

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Xato-jurnali]]
- [[Ish-oqimlari]]
- [[Kop-korxonali-izolyatsiya]]
- [[Integratsiyalar]]

<!-- avto-bog'liq:end -->
