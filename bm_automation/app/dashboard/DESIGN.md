# BM Dashboard — Dizayn tizimi

> **Manba (source of truth):** `web/index.html` (bitta fayl: inline CSS + JS).
> **Yangilangan:** 2026-10-14. **Metodika:** 3 qavatli token arxitekturasi (primitive → semantik → komponent).
> Chiziq raqamlari shu sanadagi fayl holatiga tegishli; qidiruvda blok izohlari (`THEME ENGINE`, `board`, `Kirish kartasi`) ishonchliroq.

Bu hujjat dashboard'ning vizual tili — ranglar, shriftlar, o'lchamlar, komponentlar — bo'yicha yagona qo'llanma. Yangi sahifa yoki komponent qo'shganda avval shu yerdan qarasin; mavjudidan chetlashish kerak bo'lsa, sababi bilan hujjatga yozilsin.

---

## 1. Brend va uslub tili — "Jo'nash taxtasi"

Dashboard avtovokzal **jo'nash taxtasi** (departure board) metaforasi asosida qurilgan: to'q sirt, indigo urg'u, signage shrift bilan yirik raqamlar, split-flap aylanma animatsiyasi va nozik ornament chiziqlar.

Uchta imzo element:

| Element | Nima | Qayerda |
| --- | --- | --- |
| **Signage tipografika** | Big Shoulders Display — faqat sarlavha/raqamlar uchun "tablo" shrifti | `.page-hero h2`, `.kpi .num`, `.brand-mark`, `.auth-mark`, `.auth-title` |
| **Split-flap** | KPI raqami almashganda yuqoridan `rotateX` bilan "tablo" aylanishi | `.flap` (193–237-qatorlar) |
| **Yo'l varaqasi** | Kirish kartasi — perforatsiya chizig'i va chekka o'yig'i bilan hujjat ko'rinishi | `.auth-card` / `.wb-*` bloki (960–1010-qatorlar) |

Ovoz ohangi: foydalanuvchi interfeysi **to'liq o'zbek tilida**, qisqa buyruq uslubida (`KIRISH`, `Saqlash`, `Barcha kanallar`). Ortiqcha dekoratsiya yo'q — bezak faqat "signature" elementlarda, qolgan hamma joy tinch va xizmatkor.

---

## 2. Token arxitekturasi — 3 qavat

```
┌────────────────────────────────────────────────────────┐
│ 3. KOMPONENT qatlami      komponent ichidagi o'lchamlar │
│    .kpi .num: 46px, .button: min-height 34px, ...       │
├────────────────────────────────────────────────────────┤
│ 2. SEMANTIK qatlam        maqsad bo'yicha nomlangan var │
│    --bg-panel, --accent, --ink, --line, ...             │
├────────────────────────────────────────────────────────┤
│ 1. PRIMITIVE qatlam       xom qiymat (hex, rgba)        │
│    #6366f1, #00b894, rgba(148,163,184,.14), ...         │
└────────────────────────────────────────────────────────┘
```

Hozirgi faylda: **2-qavat to'liq rasmiylashtirilgan** (yagona joyda), 1 va 3-qavatlar ichki (inline) — bu normal, lekin quyidagi qoidalar bilan boshqariladi.

### 2.1. Qayerda yashaydi (fayl xaritasi)

| Blok | Qatorlar | Vazifa |
| --- | --- | --- |
| **THEME ENGINE** (asosiy palitra) | ~659–767 | **Yagona kanonik token bloki.** `:root, :root[data-theme="dark"]` = tun (default), `:root[data-theme="light"]` = kun. Barcha rang/sirt/soya qarorlari shu yerda |
| **KUNDUZGI REJIM qatlami** | ~1094–1132 | Kun rejimi "tiniq sirt" qoidalari: glass (backdrop-filter) butunlay o'chirilgan, kontrastli matn ranglari, och footer/modal fonlari. Yangi hardcode qora sirt qo'shsangiz — shu blokka kun varianti ham yoziladi |
| Umumiy doimiylar (eski `:root`) | ~45–64 | Radius, `--ease`, shrift stack'lari, `--on-accent` shu yerdan; **palitra var'lari (eski `--accent: #00b894` va h.k.) o'lik** — theme engine ularni bosib ketadi, namuna sifatida ishlatmang |
| Komponent qoidalari | 107–590 (eski) + 700–960 (bm-ux qatlami) | Komponent o'lchamlari; bm-ux bloki komponentlardan keyin kelib, ularni faqat token var'larga qayta bog'laydi |

**Qoida:** yangi rang/sirt var faqat THEME ENGINE blokida, **ikkala rejimda ham** e'lon qilinadi. Komponent CSS'ida hex/rgba yozilmaydi — hammasi `var(--...)`.

### 2.2. Semantik tokenlar (kanonik ro'yxat)

#### Sirtlar (bg)

| Token | Tun (dark) | Kun (light) | Vazifa |
| --- | --- | --- | --- |
| `--bg-deep` | `#070b11` | `#f2f5f9` | Sahifa foni, eng chuqur qavat |
| `--bg-panel` | `#0e151e` | `#ffffff` | Panel/karta yuzasi |
| `--bg-raised` | `#131d28` | `#eef1f6` | Ko'tarilgan qavat (gradient boshi, hover fon) |
| `--bg-hover` | `#182535` | `#e3e9f2` | Hover fon (umumiy) |
| `--bg-chip` | `rgba(15,23,32,.62)` | `#f4f6fa` | Chip/pill/ghost tugma foni |
| `--bg-glass` | `rgba(14,21,38,.82)` | `#ffffff` | Panel/dialog/karta oynasi foni. **Kun rejimida — tiniq oq (glass yo'q):** oynak effekt faqat tun rejimida |
| `--bg-input` | `#0a121b` | `#ffffff` | Input/select foni |
| `--bg-head` | `#0b131d` | `#eef2f7` | Jadval sarlavha katagi, sticky ustun |
| `--bg-row-hover` | `rgba(103,232,249,.035)` | `rgba(99,102,241,.05)` | Jadval qatori hover |
| `--topbar-bg` | `rgba(7,11,17,.82)` | `#ffffff` | Topbar (tun: blur bilan; kun: tiniq oq) |
| `--sidebar-bg` | `rgba(10,14,20,.92)` | `#ffffff` | Chap rail foni (tun: blur bilan; kun: tiniq oq) |

#### Matn (ink)

| Token | Tun | Kun | Vazifa |
| --- | --- | --- | --- |
| `--ink` | `#edf5f7` | `#1e293b` | Asosiy matn |
| `--ink-soft` | `#e7f0f2` | `#253244` | Input qiymati kabi yumshoqroq matn |
| `--ink-strong` | `#ffffff` | `#0f1520` | Sarlavha / eng kuchli matn |
| `--muted` | `#8190a3` | `#5b6b7d` | Yordamchi matn (label, foot) |
| `--muted-2` | `#718096` | `#64748b` | Ikkinchi darajali yordamchi |
| `--muted-3` | `#546478` | `#94a3b8` | Eng sust (placeholder, izoh) |

#### Urg'u semantikasi (indigo = brend, teal = OK, alert = xato, warn/amber = ogoh)

| Token | Vazifa | Tun | Kun |
| --- | --- | --- | --- |
| `--accent` | Brend/faol element | `#6366f1` | `#6366f1` |
| `--accent-strong` | Gradient oxiri, bosilgan holat | `#4f46e5` | `#4f46e5` |
| `--accent-soft` / `--accent-soft-border` | Shaffof fon / chegara | `rgba(99,102,241,.14)` / `.34` | `.10` / `.35` |
| `--accent-text` | Indigo matn (dark fonda o'qiladigan) | `#a5b4fc` | `#4338ca` |
| `--accent-ink` | Brend fon ustidagi matn | `#ffffff` | `#ffffff` |
| `--teal` / `--teal-strong` | Muvaffaqiyat / onlayn | `#00b894` / `#00a484` | `#00a484` / `#00795f` (kun: matnda `--teal-text` ishlatiladi) |
| `--teal-soft` / `-soft-border` / `--teal-text` | Teal oilasi | `.12` / `.3` / `#4fd8bd` | `.10` / `.32` / `#047857` |
| `--alert` / `--alert-strong` | Xatolik / kechikish | `#e17055` / `#e0643f` | `#e17055` / `#d95b3c` |
| `--alert-soft` / `-soft-border` / `--alert-text` | Alert oilasi | `.1` / `.3` / `#f0967f` | `.08` / `.30` / `#c44e30` |
| `--warn` | Ogohlantirish matni | `#f3ba60` | `#b45309` |
| `--amber` | Signage urg'u (eski amber) | `#fdcb6e` | `#fdcb6e` |

#### Chiziqlar, fokus, soya

| Token | Tun | Kun |
| --- | --- | --- |
| `--line` / `--line-strong` / `--line-soft` | `rgba(148,163,184,.14/.28/.08)` | `rgba(99,102,241,.12/.28/.07)` |
| `--ring` | `0 0 0 3px rgba(99,102,241,.2)` | `0 0 0 3px rgba(99,102,241,.16)` |
| `--shadow-sm/md/lg` | qora soyalar (`0 8px 24px rgba(0,0,0,.20)` …) | indigo tusli yumshoq soyalar |
| `--glow` / `--orn-line` / `--orn-star` | neon/ornament | indigo variantlari |
| `--scroll-thumb` | `#2c3442` | `#b9cfe9` |
| `--grid-overlay` | `rgba(255,255,255,.5)` | `rgba(30,41,59,.045)` |

#### Qavat-1 (primitive) — asosiy xom qiymatlar

`#6366f1` indigo-500 · `#4f46e5` indigo-600 · `#00b894`/`#00a484`/`#00795f`/`#047857` teal shkalasi · `#e17055`/`#e0643f`/`#d95b3c`/`#c44e30` coral · `#fdcb6e` amber · `#f3ba60`/`#b45309` warn · `#070b11`/`#0e151e`/`#131d28`/`#182535` slate-tun · `#f2f5f9`/`#ffffff`/`#eef1f6`/`#e3e9f2`/`#f4f6fa` slate-kun · matn shkalasi `#edf5f7 → #546478` (tun) va `#1e293b → #94a3b8` (kun).

Qoida: bitta xom qiymat ikki va undan ortiq semantik var ichida ishlatilsa — primitive sifatida ajratish nomzodi. Hozircha bunday ehtiyoj yo'q; kerak bo'lsa shu jadvalga qo'shiladi.

### 2.3. Qavat-3 (komponent) tokenlari — tavsiya

Hozir komponent o'lchamlari to'g'ridan-to'g'ri komponent qoidasida yozilgan (masalan `.kpi .num { font-size: 46px }`). Bu kichik tizim uchun yetarli. Kelgusi ishlarda per-komponent sozlash kerak bo'lsa, quyidagi nomlash naqshiga o'tish mumkin: `--{komponent}-{xususiyat}-{holat}` (masalan `--button-bg-hover`, `--card-padding`). Yangi komponent yozganda minimum: o'lcham/rangni to'g'ridan-to'g'ri semantik var'lardan olish, hech bo'lmasa shu naqshda lokal var ochish.

---

## 3. Tipografiya

**Yuklangan shriftlar** (`fonts.googleapis.com` linkida): Inter (400–900), JetBrains Mono (400–700), **Big Shoulders Display (700–900)**, IBM Plex Mono (400–600), IBM Plex Sans (400–600). `display=swap` + `media="print" onload` nonblokirovka yuklash.

| Rol | Stack | Vazifa |
| --- | --- | --- |
| `--font-ui` | `"Inter", "Segoe UI", sans-serif` | Butun interfeys matni (`body` 14px / 1.55) |
| `--font-mono` | `"JetBrains Mono", "IBM Plex Mono", monospace` | Raqamli mikro-matn: label, `th`, pill, clock, `.mono` (`tabular-nums` bilan) |
| **Signage** | `"Big Shoulders Display", "Inter", sans-serif` | Faqat "tablo" elementlari (pastda) |

### 3.1. Signage qoidasi va `!important` mexanikasi ⚠️

THEME ENGINE 704–712-qatorlarda barcha matn rollarini `!important` bilan majburlaydi:

```css
:is(h1,h2,h3,h4,h5) { font-family: var(--font-display) !important; }   /* --font-display = "Inter" */
```

Natija: oddiy `font-family` deklaratsiya (importanti yo'q) har qanday sarlavhada **yutqazadi**. Shuning uchun signage kerak bo'lgan elementga aynan shunday yoziladi:

```css
.kpi .num { font-family: "Big Shoulders Display", "Inter", sans-serif !important; }
```

**Ruxsat etilgan signage ro'yxati** (faqat shular):
`.page-hero h2` (589) · `.kpi .num` (714) · `.brand-mark` (722) · `.auth-mark`, `.auth-title` (waybill bloki 960–1010).

**Qilmang:**
- `--font-display` ni "Big Shoulders"ga o'zgartirmang — u holda butun sarlavha tizimi (panel h2, sidebar, clock) buziladi; signage faqat nuqtaviy, `!important` bilan qo'yiladi.
- Yangi sarlavhaga signage'ni "ko'rinadi-chi" deb qo'shmang — bu taxtaning imzo elimenti, u faqat yuqoridagi ro'yxatda yashaydi. (Diqqat: `.panel h2` hozir `:is(h1..h5)` qoidasi bilan **Inter** chiziladi — bu atayin: signage yagona o'ringa jamlangan.)

### 3.2. O'lcham shkalasi (amaldagi)

| O'lcham | Qo'llanish |
| --- | --- |
| 46px / 900 | `.kpi .num` (signage, `line-height: 1.05`) |
| 34px / 800 | `.page-hero h2` (uppercase, `letter-spacing: .5px`) — mobil 24px (≤1100px), 21px (≤620px) |
| 27px / 800 | `.auth-title` (waybill sarlavhasi) |
| 22px / 600 | `.clock-time` (mono) |
| 20px / 800 | `.panel h2` (`letter-spacing: 1px`, tagida 54px amber chiziq), `.brand-name`, `.metric-box b` (mobil 24px) |
| 15px | Auth input qiymati (fokusda oson o'qilishi uchun) |
| 14px | `body` asosiy matn |
| 13px | Jadval kataklari, `.problem` matni, tugma atrofi matni |
| 12–12.5px | Mono mikro-matn: input 12px, `.button` 600 12px, `.pill-tw` 12px |
| 11px | `.kpi .label` (uppercase, `letter-spacing: 1.2px`), `.kpi .foot`, `.clock-date`, `.tag`/`.pill-status` 10–11px |
| 10px | `th` sarlavhalar (uppercase, `letter-spacing: 1.6px`), `.wb-lb`, `.auth-foot` |

Raqamli ustunlar va soatlarda doim `.mono` (tabular-nums) — layout sakramaydi.

---

## 4. O'lcham, radius, soya, harakat

### 4.1. Bo'shliq ritmi

Asosiy ritm **18px** (panel padding/margin, `.cols` gap). Yordamchi qadamlar: 4 · 6 · 8 · 10 · 12 · 14 · 16 · 18 · 20 · 26. Karta ichi: karta padding 14–18px, jadval katagi `10px 12px`, tugma `8px 12px`. Mobil (≤620px): panel padding 10px gacha qisqaradi.

### 4.2. Radius

| Token / qiymat | Qo'llanish |
| --- | --- |
| `--radius-sm: 8px` | Input, tugma, badge, kichik kartalar |
| `--radius: 12px` | `--radius` (default): panel, KPI, `.err`, `.empty` |
| `--radius-lg: 16px` | Dialog, auth karta |
| `999px` | Pill/tag/status (to'liq yumaloq) |
| 5px / 9px / 10px | Mayda: `.doc-mark` 5, `.brand-mark` 9, `.icon-button` 10 |

### 4.3. Soya (elevatsiya)

`--shadow-sm` (panel/KPI tinch holat) → `--shadow-md` (hover, ko'tarilish) → `--shadow-lg` (suzuvchi: dialog, auth karta). Dark rejimda soyalar to'q qora, light'da indigo tusli yumshoq. Fokus: `--ring` (3px indigo halqa, inputs) + global `outline: 2px solid var(--accent)` keyboard uchun.

### 4.4. Harakat (motion)

| Davomiylik | Qo'llanish |
| --- | --- |
| `.14–.16s` | Mikro: hover rang/soya, pill, input border |
| `.18s` | Holat o'tishi: panel/KPI hover, switch `.18s` |
| `.2s` | Tema almashinuvi (body, panel, input… ro'yxati 939–942) |
| `.25s` | `fadeDown` (xato banneri chiqishi) |
| `.5s` | `.flap` split-flap aylanishi (KPI qiymat almashganda; kechikishlar 0/.12/.24/.36s) |
| `.8s` | KPI progress bar to'lishi (`width`) |

Yagona easing: `--ease: cubic-bezier(.4,0,.2,1)`. `prefers-reduced-motion` da flap/banner/pulse o'chadi (232–237). Animatsiya **faqat ma'no berganda**: raqam yangilanishi (flap), holat o'zgarishi (soya), yuklanish (bar).

### 4.5. Z-index shkalasi

Sticky jadval ustuni: 1 (katak) / 2 (`th`) → topbar: 50 → dialog: 200 → auth overlay: 300. Yangi qavat qo'shsangiz shu zinapoyaga qo'shing, oraliq qiymat o'ylab topmang.

---

## 5. Komponent spetsifikatsiyalari

Spetsifikatsiya formati: **Default / Hover / Active / Focus**. Disabled holat alohida kerak bo'lsa — `opacity .5` + `cursor: not-allowed` (yagona naqsh).

### 5.1. KPI kartasi (`.kpi`)

Jo'nash taxtasining yuqori qatori — 4 ta karta (`.hero` grid).

| Xususiyat | Default | Hover |
| --- | --- | --- |
| Fon | `linear-gradient(180deg, var(--bg-raised), var(--bg-panel) 55%)` + status gradiyenti (`--teal/--amber/--alert` 10–12% color-mix) | o'zgarmaydi |
| Chegara | `1px solid var(--line)` | `var(--line-strong)` |
| Radius / padding | `--radius` (12px) / `14px 16px 16px` | — |
| Soya | `--shadow-sm` | `--shadow-md` + `translateY(-2px)` |
| Tepqi chiziq | `::before` 3px, rang: teal/amber/alert status bo'yicha | — |
| Raqam | `.num` — 46px / 900 signage; rang status: teal/amber/alert | flap animatsiya qiymat o'zgarganda |
| Label / foot | 11px mono uppercase (`--muted`) / 11px mono | — |
| Ikonka | `.kpi-ic` 26px chiziqli SVG (stroke 1.5, `currentColor`), o'ng yuqorida | — |
| Progress bar | `.bar` 5px, `999px`, ichida `i` (`--teal/--amber/--alert`), `width .8s` | — |

Hover'da karta ko'tarilmasin desa — `transition`ni olib tashlamang, faqat shadow'ni `--shadow-sm` da qoldiring; asosiy naqsh saqlanadi.

### 5.2. Panel (`.panel`)

| Xususiyat | Default | Hover |
| --- | --- | --- |
| Fon | `linear-gradient(145deg, var(--bg-raised), var(--bg-panel))` (yangi) — eski chiziqli overlay o'lik | — |
| Chegara | `1px solid var(--line)` | `--line-strong` |
| Radius / padding / margin | 12px / 18px / `margin-bottom: 18px` | — |
| Soya | `--shadow-sm` | `--shadow-md` |
| Sarlavha `h2` | Inter 800 20px, `letter-spacing: 1px`, tagida `--line-soft` chizig'i + 54px amber gradient chiziqcha (`h2::after`) | — |
| `h2 .hint` | IBM Plex Sans 400 12px `--muted`, chapdan 8px | — |

### 5.3. Tugma (`.button`)

| Xususiyat | Default | Hover | Active | Focus |
| --- | --- | --- | --- | --- |
| Fon | `linear-gradient(135deg, var(--accent), var(--accent-strong))` | o'zgarmaydi | o'zgarmaydi | — |
| Matn | `--accent-ink` (oq), 600 12px mono, `letter-spacing: .3px` | — | — | — |
| Chegara | transparent | transparent | — | — |
| Soya | `0 6px 18px var(--accent-soft)` | `0 8px 22px var(--accent-soft)` | — | — |
| Transform | — | `translateY(-1px)` | `translateY(0)` | — |
| Outline | — | — | — | `2px solid var(--accent)`, offset 2px |
| O'lcham | `padding: 8px 12px`, `min-height: 34px`; **mobil ≤640px: min-height 42px** | | | |

Variantlar:

| Variant | Default | Hover |
| --- | --- | --- |
| `.button.ghost` | fon `--bg-chip`, chegara `--line-strong`, matn `--muted-2` | fon `--bg-raised`, chegara `--accent-soft-border`, matn `--accent-text` |
| `.button.danger` | fon `--alert`, chegara `--alert`, matn `#fff` | soya `0 4px 14px` alert tusida |

Bitta ekranda bitta primary (gradient) tugma; qolganlari ghost.

### 5.4. Input / Select (`.input`, `.select`)

| Xususiyat | Default | Focus |
| --- | --- | --- |
| Fon / chegara | `--bg-input` / `--line-strong` | chegara `--accent` |
| Matn | `--ink-soft`, 12px mono | — |
| Placeholder | `--muted-3` | — |
| Radius / padding / balandlik | 8px / `8px 10px` / `min-height: 34px` (mobil 42px) | — |
| Soya | — | `--ring` (`0 0 0 3px` indigo) |

Xato holati inputda: chegara `--alert` + `.err` bannerda izoh. Parol inputi (waybill): `letter-spacing: 5px`, 15px mono.

### 5.5. Pill va teglar

| Komponent | Default | Hover / Active | Ishlatilishi |
| --- | --- | --- | --- |
| `.pill` (topbar filtri) | chegara `--line-strong`, matn `--muted`, mono 12px, `7px 14px`, 999px | hover: fon `--bg-raised`, matn `--ink`, `translateY(-1px)`; active: amber fon `#10131A` matn | Ko'p korxonali rejimda filtr |
| `.pill-tw` (sahifa filtri) | fon `--bg-chip`, chegara `--line-strong`, matn `--muted` | hover: `--bg-raised` + `--accent-soft-border`; active: fon `--accent-soft`, matn `--accent-text`, 600, glow soya | status/segment filtrlari |
| `.tag` | mono 10px, 999px, chegara `--line` | — | `.good` teal / `.warn` amber / `.bad` alert (soft fon + chegara oilasi) |
| `.pill-status` | mono 11px, 999px | — | `.done` teal / `.late` alert / `.run` warn / `.gray` muted |

Rang hech qachon yolg'iz ma'no tashimaydi — pill/tag matni ham yoziladi (`Kechikdi`, `Yaxshi`).

### 5.6. Switch (`.switch`)

38×21px, slider `--line-strong`, knob 15px oq (`top/left: 3px`), o'tish `.18s`. Checked: fon `#2ecc71`, knob `translateX(17px)`. Yonida `.sms-route-on/off` nuqta (9px, `#2ecc71`/`#e74c3c`). Focus: `input:focus-visible + .slider` → global outline halqasi.

### 5.7. Jadvallar (`.board` / `.table` / `.registry`)

| Xususiyat | Qiymat |
| --- | --- |
| Katak | `10px 12px` (registry `9px 12px`), 13px, `border-bottom: 1px solid var(--line-soft)` |
| `th` | mono 10px, uppercase, `letter-spacing: 1.6px`, `--muted`, fon `--bg-head` |
| Qatorlar | juft qator: `--line-soft` fon; hover: `--bg-row-hover` |
| **Sticky birinchi ustun** | `position: sticky; left: 0`; `th` z-2 fon `--bg-head`, `td` z-1 fon `--bg-panel`, hover'da `--bg-raised`; o'ng chetda `2px 0 0 var(--line-soft)` soya |
| Scroll | `.table-wrap` gorizontal scroll (thin); ≤640px `min-width: 680px` |
| Badge (`.board .badge`) | 28×28px, 8px radius, mono 12px 600, rang `--accent`, fon `--bg-panel` |
| Raqam katak | `.mono-cell` 12px mono; foiz `.pct` 12px 600 (teal/amber/alert) |

### 5.8. Navigatsiya (rail)

| Element | Spets |
| --- | --- |
| Topbar | sticky, `--topbar-bg` + `blur(10px)`, `padding: 12px 20px`, z-50 |
| Brand | `.brand-mark` 34×34 (waybill'da boshqa o'lcham), indigo gradient, 1.5px oq inner ring, signage "BM"; `.brand-name` 20px 800; `.brand-sub` mono 9px uppercase |
| Rail (`.side-nav`) | fon `--sidebar-bg`; faol band `::after` bilan `❖` belgisi (≤1200px yashiriladi) |
| Subnav | mono 13px, faol: 2px pastki chegara `--accent`; hover matn `--accent-text` |
| Refresh belgisi | `.nav-section-note` 9–10px mono `--muted-3` |

Mobil: `body.nav-open` bilan drawer; `≤620px` guruhlar gorizontal ochiladi (916–918).

### 5.9. Kirish kartasi — "Yo'l varaqasi" (`.auth-card`)

| Element | Spets |
| --- | --- |
| Overlay | `--bg-deep` fon + nozik SVG naqsh (46px, `--muted` 6% opacity), z-300 |
| Karta | `min(420px, 100%)`, radius 16px, `--shadow-lg`, `overflow: hidden`, fon `--bg-panel` |
| `.wb-head` | 20px 24px padding, gradient `--bg-raised → --bg-panel`; `.auth-mark` 58×44 (signage "BM", indigo gradient, 2px oq ring + 3.5px to'q ring); `.auth-title` 27px signage uppercase; `.auth-sub` mono 11px `--muted` ("Hisobingiz bilan kiring") |
| `.wb-perf` | 2px `dashed var(--line-strong)` + ikki chetda 16px o'yilgan doira (fon `--bg-deep`) — perforatsiya illyuziyasi |
| `.wb-lb` | mono 600 10px uppercase, `letter-spacing: 1.6px`, `--muted-2`, o'ngga nuqtali "leader" chizig'i |
| Inputlar | min-height 42px; parol `letter-spacing: 5px` |
| `.auth-btn` | to'liq kenglik, min-height 44px, `KIRISH` (uppercase, `letter-spacing: 2px`) |
| `.auth-err` | mono 12px `--alert-text`, markazda, `role="alert"` (bo'sh holatda ham joy egallaydi — layout sakramaydi) |
| `.auth-foot` | mono 10px, `letter-spacing: 2px`, yuqorida `--line-soft` chiziq |

Mobil ≤420px: paddinglar 16px/18px'ga qisqaradi.

### 5.10. Dialog (`.driver-dialog`)

`<dialog>` elementi, `min(1180px, calc(100vw - 28px))`, `--radius-lg`, fon `--bg-panel`, chegara `--line-strong`. Backdrop: `rgba(2,6,12,.55)` + `blur(4px)`. Sarlavha qismi sticky. Yopish tugmasi `.icon-button` (34×34, 10px radius). Fokus `<dialog>`ning o'zida ham global halqa bilan ko'rinadi.

### 5.11. Bo'sh holat va xato

| Komponent | Spets |
| --- | --- |
| `.empty` | markazda, `--muted`, 13px, `26px 18px` padding, 1px `dashed var(--line)` chegara, fon `--line-soft` |
| `.err` (banner) | fon `--alert-soft`, chegara `--alert-soft-border`, chapda 3px `--alert` chizig'i, matn `--alert-text`, `fadeDown .25s` |
| `.sync-result` | teal oilasi + chap 3px `--teal` chizig'i, mono 12px, `white-space: pre-wrap` |
| `.problem` bandi | chapda 3px rangli bar (serious `--alert` / warn `--amber`), hover fon `--line-soft` |
| `.driver-message` | `.ok` → teal oilasi; default → alert oilasi |

### 5.12. Floating Action Button (`.fab` / `.fab-menu` / `.fab-item`)

| Xususiyat | Default | Hover | Active |
| --- | --- | --- | --- |
| FAB fon | `linear-gradient(145deg, var(--accent), var(--accent-strong))` | `rotate(45deg) translateY(-2px)` + glow soya | — |
| FAB o'lcham | `56×56px`, radius `16px`, `box-shadow: var(--glow)` | — | `rotate(45deg)` |
| Menu fon | `var(--bg-glass)` + `backdrop-filter: blur(12px)` (kun: blur'siz, tiniq oq) | — | — |
| Item (`.fab-item`) | `border: 1px solid var(--line-strong)`, fon `--bg-glass`, matn `--ink` | `border: var(--accent-soft-border)`, fon `--accent-soft`, matn `--accent-text`, `translateX(-3px) scale(1.03)` + glow | — |
| Ikonka | `18px`, `--accent-text` | — | — |

Animatsiya: `fabIn` (`.25s` stagger), `fabBackdrop` (blur 3px overlay — kun rejimida blur'siz, `rgba(15,23,42,.4)` qoraytirish).

### 5.13. Skeleton Loader (`.sk`)

| Variant | O'lchamlar | Uslub |
| --- | --- | --- |
| `.sk-kpi` | `height: 86px`, radius `var(--radius)` | `box-shadow: var(--shadow-sm)` |
| `.sk-row` | `height: 18px`, kengliklar `%` | — |
| `.sk-block` | `height: 200px`, radius `var(--radius)` | — |
| `.sk-card` | `height: 120px`, radius `var(--radius)` | — |

Barchasi: `animation: skShimmer 1.3s var(--ease) infinite`, gradient `var(--bg-hover) → var(--bg-raised) → var(--bg-hover)`, chegara `1px solid var(--line-soft)`.

### 5.14. Floating Labels (`.field`)

| Holat | Label pozitsiyasi |
| --- | --- |
| Default (bo'sh, fokusda emas) | `left: 10px; top: 50%; transform: translateY(-50%)` |
| Fokus / to'ldirilgan (`.floated`) | `transform: translateY(-130%) scale(.78)`, rang `var(--accent-text)`, fon `var(--bg-deep)`, `padding: 0 4px` |

Input padding: `padding-top: 14px; padding-bottom: 10px` (label uchun joy).

**Qamrov qoidasi (muhim):** floating pattern faqat matnli boshqaruvli maydonlarga qo'llanadi — `.float` klassini JS (`_initFloatLabels`) qo'shadi, u faqat `input` (checkbox/radio emas) / `textarea` / `select` bo'lgan `.field`larga beriladi. `label.check-field` va matnli boshqaruvsiz sarlavha label'lari oqimda (static) qoladi — absolyut pozitsiyalash ularga tegilmaydi. `select` doim `floated` (select matni doim ko'rinadi, sarlavha doim tepada). Qiymat JS orqali programmatik qo'yilsa (`el.value = ...`), `input` hodisasi yuborilishi shart — aks holda label markazda qolib qiymat ustiga tushadi.

### 5.15. Custom Checkbox / Radio (`.check-field input`)

| Xususiyat | Default | Checked | Focus / Hover |
| --- | --- | --- | --- |
| Checkbox | `18×18px`, chegara `var(--line-strong)`, fon `var(--bg-glass)`, radius `6px` | fon `linear-gradient(var(--accent), var(--accent-strong))`, chegara `var(--accent-strong)`, `::after` oq check (rotate 45deg) | chegara `var(--accent-soft-border)`, glow soya |
| Radio | `18×18px`, chegara `var(--line-strong)`, fon `var(--bg-glass)`, radius `50%` | chegara `var(--accent-strong)`, `::after` 8px oq doira (markazda), glow soya | — |

---

## 6. Kun/Tun (light/dark) rejim qoidalari

1. **Manba:** `localStorage['bm-theme']`; default — tun; `html[data-theme="light"]` kun rejimini yoqadi (`color-scheme` ham almashtiriladi).
2. Yangi komponent **ikkala rejimda ham** tekshiriladi — token var ishlatilsa avtomatik moslashadi, aks holda bu bug.
3. Light rejimda fon oq, matn slate, chegara indigo tusli; dark'da sirt `#070b11→#182535` zinapoyasi, chegara slate shaffof.
4. Tema almashish o'tishi avtomatik (sirtlarga `.2s`) — yangi asosiy sirt qo'shsangiz shu ro'yxatga kiriting.
5. `--amber: #fdcb6e` ikkala rejimda bir xil — u fon (chip) uchun mo'ljallangan, matn sifatida ishlatilsa faqat to'q fon ustida va minimal.
6. **Kun rejimi — tiniq (solid) dizayn:** glassmorphism faqat tun rejimida qoladi. Kun rejimida (a) barcha `backdrop-filter` o'chirilgan (KUNDUZGI REJIM bloki), (b) `--bg-glass` — tiniq `#ffffff`, topbar/sidebar ham oq, (c) mayda status matnlari (`.pill-status`, `.tag`, `.pct`, `.kpi .num.*`) `-text`/to'q variantlarga bog'langan (WCAG AA), (d) footer va jadval sarlavhalari och tokenlarda. Yangi `backdrop-filter` yoki qora hex qo'shsangiz — kun rejimi uchun override yozmasangiz, u kunda ham ko'rinadi (bug).

---

## 7. Accessibility qatlami (majburiy minimum)

- **Fokus:** keyboard fokusi har doim ko'rinadi — global qoida: `outline: 2px solid var(--accent); offset: 2px` (928–934); sichqoncha fokusi halqasiz (`:focus:not(:focus-visible)`).
- **Touch target:** mobil'da `min-height: 42px` (tugma/input/select, 1026-qator); auth tugmasi 44px. Kichik elementlar (`.driver-open`) 6×9px padding bilan 34px atrofida.
- **Kontrast:** matn `--ink`/`--muted` juftliklari fon bo'yicha tekshirilgan; `--muted-3` faqat placeholder/mikro-izoh uchun (katta matn uchun emas).
- **Holat yolg'iz rang bilan emas:** pill/tag'da matn, switch'da nuqta+rang, `aria` atributlar (auth overlay `role="dialog" aria-modal`, xato `role="alert"`).
- **Reduced motion:** `prefers-reduced-motion` — flap/err/pulse o'chadi.
- **Raqamlar:** `.mono` + `tabular-nums` — narx/soat ustunlari tekis turadi.
- **Sticky anchor:** `[id] { scroll-margin-top: 86px }` — topbar ostiga yashirinish yo'q.

---

## 8. Do / Don't

**Do:**
- Yangi rang/sirt — avval THEME ENGINE blokiga ikki rejimda var, keyin komponentda `var(--...)`.
- Yangi komponentda mavjud naqshni qayta ishlatasiz: karta = `.panel` retsepti, tugma = `.button` + variant, holat = `.pill-status`.
- Yangi sahifa qo'shsangiz: `.page-hero` + `.page-kicker` + panel ritmi (18px) — hero'dan keyin darhol kontent.
- Raqamli ustunlarga `.mono`, sarlavhaga Inter, signage'ni faqat 3.1 ro'yxatidagi elementlarga.
- O'zgartirishdan keyin: (1) inline `<script>`ni ajratib `node --check`; (2) 8093-port'da?qa= bilan ko'rish (kun+tun); (3) ID va JS funksiya nomlarini buzmaganlikni tekshirish (SPA ulanishlari).

**Don't:**
- Komponent CSS'ida raw hex/rgba yozmang (mavjud qarzlar: `.kpi::before` soya, `.switch` `#2ecc71/#e74c3c`, `.board .badge` inset, log ranglari `#94a3b8/#f87171/#fbbf24` — tegib ketganda var'ga ko'chiring).
- `--font-display`ni signage'ga aylantirmang; `!important`ni signage'dan boshqa narsaga ishlatmang.
- Element ID yoki JS funksiya nomlarini o'zgartirmang (SPA wiring ID'larga bog'langan).
- O'lik meros bloklarini "tiriltirmang": eski `:root` palitrasi, eski `.brand-mark` amber gradienti, `.kpi:hover`dagi `#3a4356` — hammasi theme engine tomonidan bosilgan, namuna sifatida olmang.
- Gradient sarlavha matni (eski hero uslubi) yoki Reem Kufi qaytarmang — olib tashlangan, kerak emas.
- Har sahifaga yangi "signature" element o'ylab topmang — tizimda bitta taxta tili bor.

---

## 9. O'zgartirish jarayoni (dizayn tegishli)

1. Token o'zgartirish → THEME ENGINE bloki; komponent o'zgartirish → bm-ux qatlami (700+) yoki tegishli komponent qoidasi.
2. Inline JS o'zgargan bo'lsa: ajratib `node --check`.
3. Lokal ko'rish: `http://127.0.0.1:8093/?qa=N` (kun va tun rejimda alohida).
4. Deploy: `index.html` scp bilan ketadi — **restart shart emas** (har so'rovda diskdan o'qiladi); deploy'dan keyin jonli md5 = disk md5 tekshiriladi.
5. Hujjat yangiligi: yangi komponent qo'shilsa shu faylga bo'lim qo'shiladi.

## 10. Sync All Days feature (2026 UI)

**Maksad:** Kunlik sinxronlash dialogida "Har bir kunni alohida sinxronlash" checkboxi qo'shish u yerda qanday amalga oshirasligini belgilash.

**Tokenlar:** checkbox holati emas, lekin u `allDays` flagini API ga yuboradi — bu token tizimi yo'q, klassik checkbox holati.

**Compnent spec:**
- **Element:** `<input type="checkbox" id="sync-all-days" checked>` (driver-dialog ichida, sync-dialog .form-grid ichida)
- **Label:** "Har bir kunni alohida sinxronlash"
- **Holati:** checked (kiysa barcha kunlar), emas (faqat belgilangan oralig'ini sinxronlash)
- **Manzil:** `index.html:2289` — `.form-grid:last-child` ichida (keyingi tugma `btn-sync-go` oldidan oldin)

**API so'rov (frontend):**
```json
POST /api/sync
{
  "from": "2026-01-15",
  "to": "2026-01-20", 
  "route": "123",        # select elementi value
  "allDays": true       # checkbox checked holati
}
```
- `allDays=true` → "barcha kunlar" oralig'i sinxronlash
- `allDays=false` (yoki absent) → faqat `from` → `to` oralig'i

**CSS/Styling:**
- Checkbox klassik `--line-strong` chegarasi va `var(--bg-glass)` ko'rib turadi (panel qaribi)
- `grid-column: 1/-1` — keng alan foydalanadi
- Active holat (`:checked`) — ikkinchi ustun rang (`var(--accent)`) bo'lishi mumkin, lekin asosiy vizual — placeholder tanovush

**Holati:**
- **Sayfa yuklaganda** checkbox `checked` deb boshlanadi (old ivzor)
- **API javobi** `ok: false bo'lsa` — checkbox holati o'zgarmaydi, xatolik ko'rinadi
- **API javobi** `ok: true` — keyingi sinxronlashlar `allDays` holati bilan saqlanadi (agar server buni saqlashi mumkin)

**Joriy DNS (Design Note):**
- Checkbox faqat ko'rinish sabzi — logikasi `runDailySync()` JavaScript funksiyasi tomonidan boshqariladi
- `allDays` qiymati `bmFetch` orqali POST ga o'tkaziladi (qurilgan, Sep 2026)
- Backend (`/api/sync` Python handler) `allDays` ni qabul qilib, keraksizsa `from` → `to` oralig'ini hisobga olmaslikka yoki "barcha kunlar" tartibida ishlatish lozim

**Test:**
- `node --check` — inline `<script>` bloklari
- Dashboard testlari: `pytest tests/test_dashboard*.py -q`
- UI: checkbox tanlangan bo'lsa "SINXRONLASH" tugmasi barcha kunlarni sinxronlaydi, maxsus oralig'i bo'lsa faqat tanlangan oraligi sinxronlaydi

**Don't:**
- Checkbox nomini `sync-daily` deb bering (bunday mavjud)
- `allDays` flagini auth yoki role-gatingga kirmang (barcha kompaniyaga kirgan foydalanuvchilar uchun pausal)
- Checkbox ning `"remember"` holatini avtomatik saqlash — faqat barcha foydalanuvchilar uchun faollashtirish kerak

**Do:**
- Yangi `id` yoki `data-ic` qiymati emas — faqat `checked/unchecked` holati muhimi
- Checkbox sahifani yangilanda (`index.html` o'zgartisi) — `scp` quyidagilar deploying kerak emas (har so'rov bilan yangilanadi)
- Backendga `allDays` qo'shish lozim bo'lsa, `routers/data.py` yoki mos regulator faylga o‘rnatish
- So‘rv bilan keyin checkbox holatini tekshirish: `document.getElementById('sync-all-days').checked`
