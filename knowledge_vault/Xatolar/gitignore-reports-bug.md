# gitignore-reports-bug

> .gitignore qoidasi muhim modulni yutib yuborgan holat.

## Semptom

- Repo'da `bm_automation/app/reports/` katalogi yo'q — lekin kod uni
  import qiladi:
  ```python
  from ..app.reports.registry import REPORTS  # ModuleNotFoundError
  ```
- `git status` fayllarni ko'rsatmaydi; `git add .` ham ularni qo'shmaydi.

## Ildiz sabab

`.gitignore` da:

```
reports/
```

Bu qoida **har qanday chuqurlikdagi** `reports/` katalogini ignore qiladi —
shu jumladan `bm_automation/app/reports/` modulni ham. Git pattern'da boshida
slash yo'q qoida barcha joyda mos keladi.

## Belgisi

- `git check-ignore -v bm_automation/app/reports/__init__.py`
  → `.gitignore:19:reports/ ...` chiqadi.
- `python -c "from bm_automation.app.reports import *"` → ModuleNotFoundError.

## Qoida

1. Paket moduli katalogini ignore qilish — **ildizga bog'langan** qoida:
   ```
   /reports/
   ```
   Faqat loyiha ildizidagi `reports/` ignore qilinadi.
2. Push'dan oldin sekret **va** "ichida ketib qolgan" modullarni
   `git status` bilan tekshiring.
3. Repo'ga ulangan nisbiy importlarni `git check-ignore` bilan tasdiqlang.

Tasdiqlovchi: `git check-ignore -v` — muvaffaqiyatli tuzatish after
`/reports/` qayta beradi hech narsa emas.