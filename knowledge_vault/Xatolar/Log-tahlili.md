<!-- avto-yaratilgan: log-tahlili -->
# Log tahlili

_Bu qayd avtomatik yangilanadi (`knowledge_grow`) — qo'lda tahrir keyingi kengaytirishda ustidan yoziladi._

_yangilandi: 2026-10-27 01:48_

Manba: `C:\Users\User\OneDrive\Документы\Default Project\logs\bm.log` (oxirgi 400 KB)

Jami **81** muammo satri, 34 xil imzo.

### 10× — `bm_automation.db | DB mavjud emas (postgres): connection timeout expired`

> 2026-10-27 00:44:26 | WARNING | bm_automation.db | DB mavjud emas (postgres): connection timeout expired

### 4× — `myai.reviewer | LLM call xatosi: LLM yo'q`

> 2026-10-27 00:53:39 | ERROR   | myai.reviewer | LLM call xatosi: LLM yo'q

### 4× — `myai.reviewer | LLM tekshiruvi ishlamadi: LLM yo'q — local tekshiruv`

> 2026-10-27 00:53:39 | WARNING | myai.reviewer | LLM tekshiruvi ishlamadi: LLM yo'q — local tekshiruv

### 4× — `myai.reviewer | Self-check rad etdi (analytics_calculate): ['Invariant buzildi: accepted (N) + not_accepted (N`

> 2026-10-27 00:53:39 | WARNING | myai.reviewer | Self-check rad etdi (analytics_calculate): ['Invariant buzildi: accepted (8) + not_accepted (5) != total_trips (10)']

### 2× — `bm_automation.db | Xavfsizlik: schedules replace-sync o'chirishi bloklandi (upstream bo'sh: N/N qator). Upstre`

> 2026-10-27 00:44:55 | WARNING | bm_automation.db | Xavfsizlik: schedules replace-sync o'chirishi bloklandi (upstream bo'sh: 2/2 qator). Upstream javoni bo'sh/noto'g'ri bo'lishi mumkin. Ruxsat uchun: BM_ALLOW_DESTRUCTIVE_RESYNC=1

### 2× — `bm_automation.db | Xavfsizlik: schedules replace-sync o'chirishi bloklandi (o'chiriladigan ulush juda katta: N`

> 2026-10-27 00:44:55 | WARNING | bm_automation.db | Xavfsizlik: schedules replace-sync o'chirishi bloklandi (o'chiriladigan ulush juda katta: 590/600 qator). Upstream javoni bo'sh/noto'g'ri bo'lishi mumkin. Ruxsat uchun: BM_ALLOW_DESTRUCTIVE

### 2× — `bm_automation.db | Xavfsizlik: driver_work_logs eski qatorlar o'chirilmadi — trips so'rovi N qatorda N chegara`

> 2026-10-27 00:44:56 | WARNING | bm_automation.db | Xavfsizlik: driver_work_logs eski qatorlar o'chirilmadi — trips so'rovi 50000 qatorda 50000 chegarasiga tegdi. Manba (trips) to'liq o'qilmagan bo'lishi mumkin, o'chirish esa haqiqiy ma'lumo

### 2× — `bm_automation.bot | sendMessage xatosi: Telegram xatosi: Bad Request: chat not found`

> 2026-10-27 00:46:04 | WARNING | bm_automation.bot | sendMessage xatosi: Telegram xatosi: Bad Request: chat not found

### 2× — `myai.orchestrator | Topshiriq uzildi (cancel): t_ext`

> 2026-10-27 00:48:29 | WARNING | myai.orchestrator | Topshiriq uzildi (cancel): t_ext

### 2× — `myai.browser_agent | DB bo'sh — API/browser ga o'tiladi`

> 2026-10-27 00:48:29 | WARNING | myai.browser_agent | DB bo'sh — API/browser ga o'tiladi

### 2× — `myai.browser_agent | API bo'sh qaytardi — browserga o'tiladi`

> 2026-10-27 00:48:29 | WARNING | myai.browser_agent | API bo'sh qaytardi — browserga o'tiladi

### 2× — `myai.browser_agent | Browser ham ishlamadi — DB ga o'tiladi`

> 2026-10-27 00:48:29 | WARNING | myai.browser_agent | Browser ham ishlamadi — DB ga o'tiladi

### 2× — `myai.master | LLM call timeout (N.2s):`

> 2026-10-27 00:48:30 | ERROR   | myai.master | LLM call timeout (0.2s):

### 2× — `myai.reviewer | LLM tekshiruvi ishlamadi: — local tekshiruv`

> 2026-10-27 00:48:33 | WARNING | myai.reviewer | LLM tekshiruvi ishlamadi:  — local tekshiruv

### 2× — `myai.test.transport | Tool timeout (5s): demo.read`

> 2026-10-27 00:48:34 | ERROR   | myai.test.transport | Tool timeout (5s): demo.read

Tuzatish topilgach qoidani `Qoidalar/` yoki `Xatolar/` qaydiga yozing — agentlar keyingi vazifada o'qiydi.

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Agentlar-arxitekturasi]]
- [[Bilim-bazasini-yuritish]]
- [[Xavfsizlik]]
- [[Test-va-tekshiruv]]

<!-- avto-bog'liq:end -->
