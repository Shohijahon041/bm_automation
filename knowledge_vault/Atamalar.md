# Atamalar

Loyihada ishlatiladigan so'zlar — bir xil ma'noda ishlatilishi uchun.

## Transport

| Atama | Ma'nosi |
|---|---|
| **Reys** | Avtobusning belgilangan yo'nalish bo'yicha bir marta qatnashi |
| **Qatnov (voiture)** | `trips` jadvalidagi bitta qator; hisobotda "reys" bilan bir xil o'lchov |
| **Avtobus** | `distinct` davlat raqami / `total_vehicles`; qatnov soni bilan **aralashtirilmaydi** |
| **Yo'nalish (route)** | Marshrut, masalan `B-80`, `12-sonli` |
| **Konechka** | Yo'nalishning boshlanish/tugash bekatlari (`start1`, `start2`); waybill'dan olinadi |
| **Waybill** | BM'dagi yo'l varaqasi — reys, haydovchi, transport ma'lumoti |
| **Qabul qilinmagan** | Tizimda tasdiqlanmagan qatnov (`NOT_ACCEPTED`) |
| **Profil** | BM'dagi korxona/yo'nalish konfiguratsiyasi (`profiles.json`, `routeVariantId`) |
| **Kompaniya (tenant)** | Panel foydalanuvchisi bog'langan korxona; ma'lumot shu chegara bilan ko'rinadi |

## Hisob-kitob

| Atama | Ma'nosi |
|---|---|
| **SKM** | 1 mashina-km narxi (so'm) |
| **Km narx (`km_rate`)** | Yo'nalish bo'yicha 1 km narxi; hisobda ustuvor |
| **Brutto** | 116-son qaror bo'yicha tashuvchiga to'lanadigan summa |
| **α, β, γ** | Bosib o'tilgan yo'l, sifat va majburiyat bo'yicha ushlab qolish koeffitsiyentlari |
| **Lr / Lf** | Reja masofa / amalda bosilgan masofa |
| **Dedup** | Bir xil xabarni takror yubormaslik (masalan, oylik Excel) |

## Tizim va xavfsizlik

| Atama | Ma'nosi |
|---|---|
| **Scope** | So'rov konteksti: foydalanuvchi, rol, kompaniya, ruxsatli yo'nalishlar |
| **Tenant guard** | Har bir API so'rovida kompaniya holatini tekshirish; xato bo'lsa `403` |
| **Fail-closed** | Shubhali holatda ruxsat **bermaslik** (xavfsiz default) |
| **Super** | Tizim egasi: ADMIN + `DASHBOARD_ADMIN_USER` yoki `data.super` bayrog'i |
| **Per-user pages** | Foydalanuvchiga ruxsat etilgan panel bo'limlari ro'yxati; rol — yuqori chegara |
| **Sections** | Panel va bot uchun yagona bo'lim kalitlari (`app/core/sections.py`) |
| **State** | `state/` papkasidagi JSON holat fayllari (sozlama, sessiya, limit) |

## Bilim bazasi

| Atama | Ma'nosi |
|---|---|
| **Vault** | `knowledge_vault/` papkasi — markdown qaydlar to'plami |
| **Qayd (note)** | Bitta `.md` fayl; papka nomi = kategoriya |
| **MOC** | Map of Content — qaydlarga kirish nuqtasi; bu loyihada `Bilim-bazasi.md` |
| **Wikilink** | `[[Qayd-nomi]]` ko'rinishidagi havola; Obsidian va panel uni bosiladigan qiladi |
| **Kategoriya** | `Qoidalar`, `Sozlamalar`, `Xatolar`, `Hisobotlar` |
| **Kunlik hisobot** | `Hisobotlar/Kunlik-hisobot-YYYY-MM.md` — avtomatik to'ldiriladi |

## Integratsiya

| Atama | Ma'nosi |
|---|---|
| **BM** | `bm.dtransport.uz` — reyslar manbasi; API hosti `bmapi.dtransport.uz` |
| **OneID** | BM'ga brauzer orqali kirish usuli (parol login ishlamaydi) |
| **Teltonika** | GPS tracker (FMB920), TCP `5027` |
| **JT808** | Qurilma bilan signaling protokoli (ro'yxat, heartbeat, buyruq), TCP `6808` |
| **JT1078** | Video media protokoli, TCP `6107` |
| **ZLMediaKit** | Video oqimini qabul qilib HTTP-FLV/HLS beradigan media server |
| **HLS / FLV** | Brauzerda video ko'rsatish formatlari |
| **Keep-alive** | BM tokenini har 12 soatda yangilab turish jarayoni |

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Ruxsatlar-va-rollar]]
- [[Arxitektura]]
- [[Integratsiyalar]]
- [[Bot-sozlamalari]]

<!-- avto-bog'liq:end -->
