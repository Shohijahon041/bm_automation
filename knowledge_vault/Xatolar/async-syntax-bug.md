# async-syntax-bug

> Dashboard bo'sh ma'lumot ko'rsatgan holat (vaqtincha "--:--:--" soat bilan).

## Semptom

- Dashboard sahifasi ochiladi, lekin **hech qanday ma'lumot ko'rinmaydi**.
- Hatto soat ham "`--:--:--`" bo'lib qotgan.
- Backend API (masalan `/api/summary`) to'g'ri ma'lumot qaytaradi (200).

## Ildiz sabab

- `web/index.html` — `saveSettings()` funksiyasida **`async` kaliti**
  tushib qolgan edi.
- Natijada script ichidagi `await postJSON(...)` **butun JS blokida
  `SyntaxError`** berdi.
- Shuning uchun `load()` (va deyarli hamma funksiya) ishga tushmadi —
  sahifa bo'sh qoldi.

## Belgisi

- Brauzer konsolida:
  `SyntaxError: await is only valid in async functions...`

## Qoida

1. JavaScript faylga o'zgartirish kiritilganda **`await` ishlatadigan
   har bir funksiya `async function`** bo'lishini tekshiring.
2. Sinov: `node --check <script-blok>.js` jshat bilan sintaksisni tekshiring.
3. API 200 bo'lishi frontend to'g'ri ishlashini kafolatlamaydi.

Tekshiruv nomi: `node --check` yoki brauzer DevTools konsoli.