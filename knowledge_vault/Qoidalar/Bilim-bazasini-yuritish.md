# Bilim bazasini yuritish

> Bu qayd — bilim bazasining o'zi qanday ishlaydi va qanday o'sib boradi.
> Bilim bazasi — `knowledge_vault/` papkasi; uni Obsidian ilovasi vault
> sifatida ochadi, panel ("Bilim bazasi" sahifasi), Telegram bot (`/vault`)
> va agentlar esa bir xil fayllardan o'qiydi.

## Kategoriyalar

| Papka | Nima saqlanadi |
| --- | --- |
| `Qoidalar/` | buzilmas qoidalar, invariantlar, arxitektura tartibi |
| `Sozlamalar/` | sozlama kalitlari, deploy va ishga tushirish tartibi |
| `Xatolar/` | tasdiqlangan xatolar va ulardan olingan darslar |
| `Hisobotlar/` | avtomatik hisobotlar, kod/holat xaritalari |

Yangi qayd faqat shu to'rt papkadan biriga tushadi
(`app/core/knowledge.py::CATEGORIES`) — boshqa papka nomi rad etiladi.

## Avtomatik kengaytirish (`knowledge_grow`)

`bm_automation/app/core/knowledge_grow.py` vault'ni o'zi to'ldiradi:

- **Kod xaritasi** — `Hisobotlar/Kod-xaritasi` (modul daraxti, qatorlar soni).
- **API endpointlar** — `Hisobotlar/API-endpointlar` (routerlardagi yo'llar).
- **Bot buyruqlari** — `Hisobotlar/Bot-buyruqlari` (`/buyruq` lar ro'yxati).
- **Testlar** — `Hisobotlar/Testlar` (fayl/`test_*` soni).
- **Muhit sozlamalari** — `Hisobotlar/Muhit-sozlamalari` (env nomi + standart).
- **DB jadvallari** — `Hisobotlar/DB-jadvallari` (`app/db/schema.py` dan).
- **Holat snapshoti** — `Hisobotlar/Holat-snapshoti` (`state/` fayllari).
- **Log tahlili** — `Xatolar/Log-tahlili` (`logs/bm.log` dagi xatolar guruhi).
- **Avto xato jurnali** — `Xatolar/Avto-xato-jurnali` (agent/tool xatolari).
- **Agent vazifalari** — `Hisobotlar/Agent-vazifalari` (bajarilgan topshiriqlar).
- **Bo'shliq to'ldirish** — yechilmagan `[[havola]]` uchun stub qayd ochiladi.

Avtomatik qaydlar ichida `<!-- avto-yaratilgan: ... -->` belgisi bor va
**qo'lda tahrirlanmaydi** — keyingi kengaytirishda ustidan yoziladi.
Doimiy bilim kerak bo'lsa, alohida oddiy qaydga ko'chiring.

### Qachon ishlaydi

1. Bot poll-tsiklida har **180 daqiqada** (sozlama:
   `KNOWLEDGE_AUTO_INTERVAL_MIN`; o'chirish: `KNOWLEDGE_AUTO=off`) — bu
   guruh eslatmalari kabi `AI_AUTO_AGENTS` ga **bog'liq emas**.
2. `python -m bm_automation vault-grow` — qo'lda bir marta.
3. `python -m bm_automation vault-status` — holat (qaydlar soni, interval).
4. Panelda **Bilim bazasi → KENGAYTIRISH** tugmasi (`POST /api/knowledge/grow`).
5. Har yangi qayd yozilganda `vault-links` avtomatik ishlaydi va qaydni
   grafga bog'laydi; butun vault uchun
   `python -m bm_automation vault-links [--dry-run]`.

## Agentlar o'rganish sikli

1. Vazifa boshlanishida agentlarga tegishli qaydlar kontekst qilib beriladi
   (`myai/knowledge_context.py::brief` — token o'xshashligi, LLM kerak emas).
2. Tool yiqilsa `lesson_hint` o'sha amalga tegishli `Xatolar/` qaydini
   qo'shadi va xato `Avto-xato-jurnali` ga imzo bo'yicha yoziladi.
3. Reviewer tasdiqlangan xatoni `vault` tool orqali `Xatolar/` ga yozadi.
4. Vazifa tugaganda `Agent-vazifalari` qaydi yangilanadi.

Ya'ni bir marta topilgan xato keyingi vazifada qayta takrorlanmaydi —
buni [[Qoidalar/Agentlar-arxitekturasi]] qaydi ham tasvirlaydi.

## Yozish qoidalari

- Sarlavha — fayl nomi; `[[Qayd-nomi]]` havolasi shu nom bo'yicha ishlaydi.
- Bitta qayd — bitta mavzu; uzun matnni ikkiga bo'lib, `[[havola]]` bilan bog'lang.
- Fakt va yo'lni yozing (`app/...`, `state/...`, buyruq) — taxmin emas.
- Sirli qiymat (parol, token, kalit) **hech qachon** yozilmaydi —
  faqat o'zgaruvchi **nomi** ([[Qoidalar/Xavfsizlik]]).
- Xato yozganda: alomat → ildiz sabab → tuzatish → qaytmaslik qoidasi.

Bog'liq: [[Bilim-bazasi]] · [[Qoidalar/Xavfsizlik]] · [[Arxitektura]]

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Agentlar-arxitekturasi]]
- [[Asosiy-invariantlar]]
- [[Xato-jurnali]]
- [[Arxitektura]]

<!-- avto-bog'liq:end -->
