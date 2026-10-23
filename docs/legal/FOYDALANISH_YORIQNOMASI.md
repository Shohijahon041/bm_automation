# BM Dashboard — foydalanuvchilar uchun yo'riqnoma

**Hujjat versiyasi:** 1.0  
**Yangilangan:** 2026-yil 21-oktabr  
**Tizim:** BM Dashboard (Fleet Command Center)

> Bu yo'riqnoma dashboard foydalanuvchilari uchun. Tizimga kirishdan oldin
> tashkilotingizning mas'ul xodimi bilan akkaunt va ruxsatlarni kelishib oling.

## 1. Tizim nima uchun kerak

BM Dashboard avtobus parki ishini bir joyda ko'rish va boshqarish uchun:

- kunlik reyslar, haydovchilar va avtobuslar holati;
- haydovchi ish kunlari, bosilgan km, qatnovlar va ish haqi;
- jarimalar, murojaatlar, hujjatlar va guvohnomalar muddati;
- oylik hisobotlar, eksport (CSV, XLS, XLSX, PDF);
- BM sayti bilan ma'lumotlarni sinxronlash;
- Telegram orqali eslatmalar va hisobotlar.

Har bir foydalanuvchi faqat o'z kompaniyasi (profili) doirasidagi
ma'lumotlarni ko'radi. Bu cheklov server tomonda ham tekshiriladi.

## 2. Rollar va ruxsatlar

| Rol | Asosiy vazifasi | Moliyaviy bo'limlar |
|---|---|---|
| ADMIN | Tizim administratori; barcha kompaniyalar va sozlamalar | Ko'radi |
| DIRECTOR | Kompaniya rahbari; keng ko'lamli ko'rish va boshqarish | Ko'radi |
| MANAGER | Kompaniya bo'yicha boshqaruv, hisobot va eksport | Ko'radi |
| DISPATCHER | Kunlik operatsiyalar, reyslar va haydovchilar | Yashirin |
| VIEWER | Faqat ko'rish | Yashirin |

Qo'shimcha ravishda har bir foydalanuvchiga alohida bo'limlar (sahifalar)
berilishi mumkin. Bo'lim yopilgan bo'lsa, unga to'g'ridan-to'g'ri havola
orqali kirish ham bloklanadi.

## 3. Tizimga kirish

1. Brauzerda tashkilotingiz bergan manzilni oching.
2. **Login** va **parol**ni kiriting.
3. **KIRISH** tugmasini bosing.

Parol talablari:

- kamida **8 belgi**, ko'pi bilan 128 belgi;
- parol login nomiga o'xshamasligi kerak;
- eng ko'p uchraydigan parollar (masalan, `12345678`, `password`) qabul qilinmaydi;
- parolni hech kimga bermang va bir nechta joyda ishlatmang.

Xavfsizlik qoidalari:

- har bir xodimga alohida akkaunt oching;
- ish stolidan chiqishda **Chiqish** tugmasini bosing;
- umumiy kompyuterda "parolni eslab qolish"dan foydalanmang;
- parol o'g'irlangan deb hisoblasangiz, darhol almashtiring va administratorga xabar bering.

Sessiya **12 soat** amal qiladi. Bir foydalanuvchi uchun bir vaqtda ko'pi bilan
**8 ta faol sessiya** saqlanadi; eng eski sessiya avtomatik yopiladi.
Parol, rol yoki kompaniya o'zgarsa, eski sessiyalar yopiladi.

## 4. Asosiy bo'limlar

### Boshqaruv paneli

Kunlik asosiy ko'rsatkichlar: faol avtobuslar, qatnovlar, haydovchilar,
bosilgan masofa, jarimalar va ogohlantirishlar. Yuqoridagi davr va yo'nalish
filtrlarini o'zgartirib, ma'lumotni yangilang.

### Yo'nalishlar

Yo'nalishlar ro'yxati, har bir yo'nalish bo'yicha avtobuslar va haydovchilar.
Yo'nalish nomi, 1 km narxi va boshqa parametrlar vakolatga qarab tahrirlanadi.

### Haydovchilar reestri

- oy tanlash (`Oy` maydoni);
- ism, ID yoki telefon bo'yicha qidirish;
- haydovchi kartasini ochish;
- jadvalni CSV, XLS, XLSX yoki PDF qilib yuklab olish;
- BM saytidan haydovchi ma'lumotlarini to'ldirish.

### Haydovchi kartasi

Kartada quyidagilar bor:

- tanlangan oy bo'yicha ish kunlari, qatnovlar, masofa, brutto, soliq,
  jarima va qo'liga tegadigan summa;
- **Ishlagan oylari** — haydovchi ishlagan barcha oylar ro'yxati. Oy nomini
  bosish orqali o'sha oy ma'lumotlariga o'tiladi;
- profil va ish sozlamalari (telefon, STIR, reyting, 1 km narxi, qora ro'yxat);
- pasport va guvohnoma ma'lumotlari hamda ularning rasmlari;
- Telegram bog'lanishi va eslatma sozlamalari;
- kunlik km qaydlari, jarimalar va qatnovlar tarixi;
- taklif va murojaatlar.

**Ishlagan oylari** bo'limi joriy oy bilan cheklanmaydi: unda qatnovlar yoki
qo'lda kiritilgan km qaydlari mavjud bo'lgan barcha oylar ko'rsatiladi.
Oyni tanlaganingizda kartadagi barcha ko'rsatkichlar, qatnovlar va km qaydlari
o'sha oy bo'yicha yangilanadi. Kartani yuklab olish ham tanlangan oy bo'yicha
amalga oshiriladi.

### Hujjatlar

Haydovchi pasporti, guvohnomasi va boshqa hujjatlar. Muddati yaqinlashgan
hujjatlar bo'yicha ogohlantirishlar chiqadi. Rasmni yuklash uchun
**YUKLASH / ALMASHTIRISH** tugmasidan foydalaning (JPG, PNG yoki WEBP, 5 MB gacha).

### Elektr energiyasi va boshqa hisob-kitoblar

Vakolatli foydalanuvchilar uchun elektr energiyasi, yo'qotishlar va boshqa
hisob-kitob bo'limlari. DISPATCHER rolida bu bo'limlar yashiriladi.

### Sozlamalar

- kompaniya profili va rekvizitlari;
- dashboard foydalanuvchilari (faqat ADMIN/DIRECTOR vakolatiga qarab);
- Telegram va avtomatik eslatmalar;
- xavfsizlik va ko'rinish sozlamalari.

## 5. Eksport

Ro'yxatlar va hisobotlarni yuklab olish mumkin:

- **CSV** — Excel yoki boshqa dasturda ochish uchun;
- **XLS / XLSX** — formatlangan jadval;
- **PDF** — chop etish uchun.

Eksport joriy filtrlar (oy, yo'nalish, qidiruv) asosida tayyorlanadi.
Haydovchi kartasidagi yuklab olish tanlangan oyni oladi.

## 6. BM sayti bilan sinxronlash

Sinxronlash BM (bm.dtransport.uz) ma'lumotlarini bazaga yangilaydi.

1. **Sinxronlash** bo'limini oching.
2. Kunlik yoki oylik sinxronlashni tanlang.
3. Jarayon tugaguncha sahifani yopmang.
4. Natijani tekshiring: yangilangan yozuvlar, xatolar va ogohlantirishlar.

Sinxronlash uzoq davom etishi mumkin. Xato bo'lsa, ekrandagi xabar va
`logs/bm.log` faylini administratorga yuboring.

## 7. Telegram eslatmalari

Haydovchi kartasida Telegram chat ID kiritilsa, tizim eslatma yuborishi mumkin.
Chat ID noto'g'ri bo'lsa, xabar yetib bormaydi. Guruh eslatmalari alohida
sozlanadi va har bir guruh uchun o'chirilishi mumkin.

## 8. Ko'p uchraydigan muammolar

| Muammo | Sababi va yechimi |
|---|---|
| Login yoki parol xato | Parolni tekshiring; 8 xato urinishdan keyin kirish 15 daqiqaga bloklanadi |
| 401 / qayta kirish talab qilinadi | Sessiya muddati tugagan; qayta kiring |
| 403 / ruxsat yo'q | Rol yoki kompaniya bo'yicha ruxsat yo'q; administratorga murojaat qiling |
| Bo'sh ro'yxat | Tanlangan oy yoki yo'nalishda ma'lumot yo'q; filtrni o'zgartiring |
| Haydovchi oylari ko'rinmaydi | Qatnov yoki km qaydlari hali kiritilmagan |
| Rasm ochilmaydi | Rasm yuklanmagan yoki fayl formati noto'g'ri |
| Sinxronlash xato berdi | BM ulanishi yoki login/parolni tekshiring; logni yuboring |

## 9. Xavfsizlik va maxfiylik

Tizimda haydovchilarning shaxsiy ma'lumotlari, pasport/guvohnoma nusxalari,
telefon raqamlari va ish haqi ma'lumotlari mavjud. Ularni faqat ish maqsadida
ishlatish, uchinchi shaxslarga bermaslik va ekranlarni himoyalash talab etiladi.

To'liq ma'lumot: `MAXFIYLIK_SIYOSATI.md`.

## 10. Yordam

Muammo bo'lsa, quyidagilarni yuboring:

- foydalanuvchi logini va kompaniya nomi;
- sahifa nomi va harakat tartibi;
- xato matni yoki ekran rasmi;
- sana va vaqt (iloji bo'lsa, log fayli).

Mas'ul kontakt: `[qo'llab-quvvatlash telefon/email]`.
