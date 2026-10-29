# Agentlar arxitekturasi (myai)

> `bm_automation/app/myai/` — LLM multi-agent qatlami. Agentlar topshiriqni
> qadamlarga bo'lib bajaradi, natijani tekshiradi va xatodan o'rganadi.

## Oqim

1. **security** — ruxsat tekshiruvi (`ROLE_PERMISSIONS`). Rolni aniqlab
   bo'lmasa **fail-closed**: ruxsat berilmaydi.
2. **planner** — topshiriqdan `steps` ro'yxati (LLM yoki deterministik
   template; `DETERMINISTIC_INTENTS` da LLM umuman chaqirilmaydi).
3. **browser / route / driver / schedule / attendance / analytics / ...** —
   qadamlar; har biri o'z tool'iga ega.
4. **reviewer** — natijani tekshiradi (matematik, to'liqlik, invariantlar,
   format). Rad etilsa tuzatish taklif qilinadi.
5. **master** — yakuniy javobni yig'adi.

Orchestrator (`myai/orchestrator.py`) qadamlarni `myai_tasks` /
`myai_task_steps` jadvallarida saqlaydi va `events.py` orqali panelga
real vaqt hodisalarini yuboradi.

## Least privilege

- Har bir agent uchun tool ro'yxati `AGENT_TOOL_ALLOWLIST` da (`None` —
  faqat master uchun hammasi).
- Vault (`vault`) **hamma agentga o'qish** uchun ochiq; **yozish** faqat
  `BaseAgent.VAULT_WRITE_AGENTS` (master, reviewer, analytics) ga.
- Idempotent bo'lmagan action'lar (`write`, `send`, `save`, `create`, ...)
  qayta urinilmaydi — ikki marta yuborilib qo'ymasin.

## Ishonchlilik

- Tool chaqiruvi timeout + eksponensial backoff (jitter bilan) bilan
  qayta uriniladi; vaqtinchalik xatolar (`TimeoutError`, `ConnectionError`,
  `OSError`) qayta uriniladi, mantiqiy xatolar darhol to'xtaydi.
- Oxirgi xato `agent.last_tool_error` da saqlanadi (tool, action, error,
  hint) va event bus'ga `success=False` bo'lib chiqadi.
- Sozlamalar: `MYAI_TOOL_TIMEOUT`, `MYAI_TOOL_RETRIES`, `MYAI_MAX_RETRIES`,
  `MYAI_TASK_TIMEOUT`.

## Bilim bazasi bilan aloqa

- Qadamdan oldin `myai/knowledge_context.py::brief` tegishli qaydlarni
  promptga qo'shadi (qoidalar → xatolar → sozlamalar tartibida).
- Xatoda `lesson_hint` o'sha amalning darsini oladi va xato
  `Xatolar/Avto-xato-jurnali` ga imzo bo'yicha yoziladi.
- Reviewer tasdiqlangan xatoni `Xatolar/` ga qayd qiladi; vazifa natijasi
  `Hisobotlar/Agent-vazifalari` ga tushadi.

Batafsil tartib: [[Qoidalar/Bilim-bazasini-yuritish]].

## Tipik xatolar

- Agentga keraksiz tool ochish (huquq kengayishi) — allowlist qoidasi.
- Promptga bilim blokini qo'shmaslik — agent eski xatoni takrorlaydi.
- Panel/bot orqali ruxsatsiz vazifa yuborish — security qadamini chetlab
  o'tmaslik kerak.

Bog'liq: [[Qoidalar/Tekshirish-qoidalari]] · [[Arxitektura]]

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Bilim-bazasini-yuritish]]
- [[Log-tahlili]]
- [[Arxitektura]]
- [[Asosiy-invariantlar]]

<!-- avto-bog'liq:end -->
