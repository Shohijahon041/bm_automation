# Oylik hisobotning takrorlanishi

> Avtomatik oqimda oylik Excel bir kunda bir necha marta yuborilgan
> holat (retry sabab).

## Semptom

- Guruhga bir xil oylik yig'ma Excel kun davomida 2+ marta keladi.
- `daily` qayta ishga tushirilganda (jadval hali tayyor bo'lmagani
  uchun retry) oylik yana yuboriladi.

## Ildiz sabab

Ilgari oylik yuborilgan-yuborilmagani **holatda saqlanmasdi**.
`daily_service` har chaqirilganda oylikni qayta yuborardi; qisman
xato (`_has_failures`) butun oqimni qayta urinishga majbur qilardi.

## Yechim

`state/daily_monthly_sent.json` + kalit:

```
route_id|YYYY-MM-DD..YYYY-MM-DD|target
```

- `_monthly_pending()` — kalit yo'q bo'lsagina yuboradi.
- `_monthly_mark()` — muvaffaqiyatli yuborilgandan keyin `atomic_write`
  bilan belgilaydi (`threading.Lock` bilan).
- `_monthly_prune()` — 45 kundan eski yozuvlarni tozalaydi.
- **Qo'lda** so'rov (`chat_id` shaxsiy chat) har doim yuboradi —
  foydalanuvchi ataylab so'ragan.
- Avtomatik oqimda `target` odatda guruh chat ID yoki `default`.

## Qoida

1. Rejalashtirilgan yuborish **idempotent** bo'lsin: kalit =
   resurs + davr + qabul qiluvchi.
2. Holat fayli atomik yozilsin (`atomic_write`), aks holda yarim
   yozilgan JSON keyingi tekshiruvni buzadi.
3. "Yuborildi" belgisi faqat **haqiqiy muvaffaqiyatdan keyin**
   qo'yiladi; xato bo'lsa keyingi urinishda qayta yuboriladi.
4. Dashboardda oylik **har doim** ko'rinadi — bu yuborish holatidan
   mustaqil ([[SMS-va-bildirishnoma-qoidalari]]).
5. Botda haydovchi oyligini ko'rish `bot_driver_salary` bilan
   boshqariladi; eski `show_driver_salary` e'tiborsiz.

Bog'liq: [[Ish-oqimlari]], [[Asosiy-invariantlar]].

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Ish-oqimlari]]
- [[SMS-va-bildirishnoma-qoidalari]]
- [[Xato-jurnali]]
- [[Sinxronizatsiya-oqimi]]

<!-- avto-bog'liq:end -->
