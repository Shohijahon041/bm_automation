# Vault path traversal (qo'shni papka)

> Bilim bazasi API'sida `../` bilan vault tashqarisiga chiqish hollari.
> Deploy oldidan topilgan va tuzatilgan.

## Semptom

- `../x.md` kabi yo'l rad etilardi, lekin qo'shni papka nomi vault
  nomidan boshlansa (`../knowledge_vault2/x.md`) — **o'tib ketardi**.
- Ya'ni himoya noto'g'ri ishlayotgan edi: ba'zi yo'llar ochiq qolgan.

## Ildiz sabab

Birinchi variant satr prefiksi bilan tekshirardi:

```python
if not str(path).startswith(str(base)):
    raise ValueError(...)
```

`Path("/opt/bm/knowledge_vault2/x.md")` satri `/opt/bm/knowledge_vault`
bilan boshlanadi — prefiks tekshiruvi **True** beradi, holbuki bu
butunlay boshqa papka.

## Yechim

`_resolve()` ikki bosqichli:

1. `base = vault_dir().resolve()` va `p = (base / rel).resolve()` —
   `..` va symlink'lar haqiqiy yo'lga aylantiriladi.
2. `if p != base and base not in p.parents: raise ValueError`.

Ya'ni tekshiruv satr emas, **haqiqiy ota-papkalar zanjiri** bo'yicha.
`p == base` (vault ildizi) ruxsat etiladi.

## Qoidalar

1. Papka chegarasini hech qachon `startswith` bilan tekshirmang.
   `Path.resolve()` + `base in p.parents` ishlatilsin.
2. Symlink ham `resolve()` bilan ochiladi — tashqariga ko'rsatuvchi
   symlink ham bloklanadi.
3. O'chirish faqat `.md` fayl bilan cheklangan; papka o'chirilmaydi.
4. Har bir yangi fayl API'sida shu tekshiruv qayta ishlatilsin.

## Test

`tests/test_knowledge_vault.py` — qo'shni papka, `..`, absolyut yo'l va
symlink holatlari. Testlar `KNOWLEDGE_VAULT_DIR` bilan izolyatsiya
qilinadi (lokal vault'ga tegmaydi).

Bog'liq: [[Xavfsizlik]], [[Atamalar]].

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Bilim-bazasini-yuritish]]
- [[Xato-jurnali]]
- [[Test-va-tekshiruv]]
- [[Asosiy-invariantlar]]

<!-- avto-bog'liq:end -->
