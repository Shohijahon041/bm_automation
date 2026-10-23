# Dashboard Development Prompt

## Maqsad
Dashboard (FastAPI + Single-file SPA) yaratish / o'zgartirish.

## Kontekst
- Fayl: `bm_automation/app/dashboard/web/index.html` (7800+ qator, inline CSS + JS)
- Tokens: 3-qavatli (`:root` → semantic → component)
- Design: Glassmorphism + Neon glow (Indigo/Teal palette)
- Responsive: Desktop (≥621px), Tablet, Mobile (≤620px bottom panel)
- Icons: Feather SVG via `<i class="ic" data-ic="...">`

## Ish Tartibi

### 1. CSS o'zgartirish
```css
/* Agar yangi rang kerak bo'lsa → THEME ENGINE blokiga (ikki rejimda) */
:root {
  --yangi-rang: #hex;
}
[data-theme="dark"] {
  --yangi-rang: #hex;
}

/* Komponentda: faqat var(--yangi-rang) ishlatish */
```

### 2. HTML/JS o'zgartirish
- Yangi element ID / funksiya nomi → SPA wiring (`index.html:5553+`) da ham yangilash
- Yangi `data-ic` ikona → `FEATHER` dict-da mavjudligini tekshirish

### 3. Validatsiya
```bash
# Inline JS extract + syntax check
python -c "
import re
html = open('index.html', encoding='utf-8').read()
scripts = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', html, re.S)
for i,s in enumerate(scripts):
    open(f'script_{i}.js','w',encoding='utf-8').write(s)
"
node --check script_0.js && node --check script_1.js && node --check script_2.js && node --check script_3.js
rm script_*.js
```

### 4. Test
```bash
pytest tests/test_dashboard*.py -q
```

## Design Tokens (2026)

| Layer | Example |
|-------|---------|
| Primitive | `#6366f1`, `rgba(148,163,184,.14)` |
| Semantic | `--accent`, `--bg-glass`, `--glow-teal` |
| Component | `.kpi .num: 46px`, `.button: min-height 38px` |

## Yangi Komponent Qo'shganda

1. `DESIGN.md` ga bo'lim qo'shish (spec §16+)
2. Light + Dark tokenlarni `:root` + `[data-theme="dark"]` ga
3. Accessibility: focus visible, touch target ≥42px, contrast, reduced motion

## Deploy
```bash
# VDS ga yuborish (FastAPI StaticFiles diskdan o'qiydi — restart shart emas)
scp index.html root@189.74.100.25:/opt/bm/bm_automation/app/dashboard/web/
# md5 tekshirish
md5sum index.html
ssh root@189.74.100.25 "md5sum /opt/bm/bm_automation/app/dashboard/web/index.html"
```