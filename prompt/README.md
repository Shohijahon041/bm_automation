# Prompt System

Bu papka Loyiha uchun standart prompt fayllarini saqlaydi.
Har bir fayl — bir xil vazifa/tur uchun tayyor prompt.

## Foydalanish

```bash
# Promptni ko'rib chiqish
cat prompt/CLAUDE_PROMPT.md

# Yangi prompt yaratish
# prompt/yangi_vazifa.md
```

## Mavjud Promptlar

| Fayl | Tavsif |
|------|--------|
| `CLAUDE_PROMPT.md` | Asosiy loyiha tanishuvi + rejalar (kopiya qilinib yuboriladigan) |

## Yangi Prompt Qo'shish

Yangi `.md` fayl yarating quyidagi shablon bilan:

```markdown
# Prompt nomi

## Maqsad
Nima qilish kerakligi.

## Kontekst
Qaysi fayllar/modullar bog'liq.

## Talablar
- Nuqta 1
- Nuqta 2

## Kutilgan natija
Nima chiqishi kerak.
```