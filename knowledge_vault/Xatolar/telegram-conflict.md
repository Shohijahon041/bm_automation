# Telegram 409 Conflict

> Bir token bilan ikki jarayon polling qilganda botning ishlamay qolishi.

## Semptom

- Bot buyruqlarga javob bermaydi yoki javoblar "sakrab" keladi.
- Logda:
  `getUpdates xatosi: Conflict: terminated by other getUpdates request`.

## Ildiz sabab

Telegram bir bot tokeniga **bitta** `getUpdates` long-poll ulanishiga
ruxsat beradi. Ikki nusxa (eski jarayon yopilmagan holda yangisi
ishga tushirilgan, yoki ikkinchi server/noutbukda bir xil token) bir
vaqtda poll qilsa — 409 Conflict.

## Kod qanday himoyalaydi

`ops.poll_forever()` 409/Conflict aniqlansa eksponensial backoff qiladi
(`_CONFLICT_BACKOFF`, maksimum 120 s). Bu cheksiz spam'ni to'xtatadi,
lekin **konfliktning o'zini hal qilmaydi** — ortiqcha jarayon
to'xtatilishi shart.

## Qoida

1. Restartdan oldin eski jarayon haqiqatan yopilganini tekshiring:
   `Get-CimInstance Win32_Process` (Windows) yoki
   `pgrep -af "bm_automation bot"` (Linux).
2. Bitta token — bitta joy. Lokal noutbuk va VDS'da bir vaqtda
   ishga tushirmang.
3. `--once` (bitta poll) smoke test uchun xavfsiz, lekin u ham
   ishlab turgan bot bilan to'qnashadi.
4. Konflikt tugagach bot o'zi tiklanishi mumkin — lekin offset
   yo'qolgan bo'lsa, o'sha oradagi xabarlar qayta ishlanmasligi mumkin.

Bog'liq: [[Bot-sozlamalari]], [[Ishga-tushirish-va-Deploy]].

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Arxitektura]]
- [[Xavfsizlik]]
- [[Bot-sozlamalari]]
- [[Integratsiyalar]]

<!-- avto-bog'liq:end -->
