# Dashboard 502 — to'liq bo'lmagan deploy

> Python moduli yangilanganda nginx 502 qaytargan holat. Ikki marta
> uchragan: `admin.py` va `api.py` import xatosi.

## Semptom

- `https://bmbrutto.uz` — **502 Bad Gateway**.
- `systemctl status bm-dashboard` — `active (running)` yoki restart
  tsikli; `journalctl -u bm-dashboard` da `ImportError` /
  `ModuleNotFoundError` traceback.
- Static HTML ochilmaydi, chunki barcha so'rovlar ilova orqali ketadi.

## Ildiz sabab

Deploy paytida **faqat o'zgargan fayl** ko'chirilgan, lekin u import
qiladigan yangi modul/funksiya serverda yo'q bo'lgan:

1. `admin.py` — yangi import qilingan modul serverda mavjud emas.
2. `api.py` (GPS) — Video/Setka ko'chirilganda import zanjiri to'liq
   bo'lmagan.

Uvicorn import bosqichida yiqiladi → port 8080 tinglanmaydi → nginx 502.

## Belgisi

```
journalctl -u bm-dashboard -n 100 --no-pager | grep -E "Error|Traceback"
```

Traceback'dagi birinchi `File ".../x.py", line N, in <module>` — aynan
yetishmayotgan bog'liqlik.

## Yechim

1. Traceback'dan yetishmayotgan faylni aniqlash.
2. Uni (va u import qiladigan yangi fayllarni) serverga yuborish.
3. `py_compile` bilan tekshirish, keyin restart.
4. `curl -sS -o /dev/null -w "%{http_code}" https://bmbrutto.uz/` → 200.

## Qoida

1. **Deploy — fayl ro'yxati emas, bog'liqlik zanjiri.** Yangi modul
   qo'shilsa, uni import qiladigan barcha fayllar ham birga ketadi.
2. Deploydan oldin lokal `py_compile` **va** import testi:
   `python -c "import bm_automation.app.dashboard.fastapi_app"`.
3. Serverda restartdan keyin darhol `journalctl` tekshirilsin; 502
   bo'lsa keyingi qadam — traceback, taxmin emas.
4. Static HTML o'zgargan bo'lsa restart shart emas, lekin Python
   o'zgargan bo'lsa **shart**.
5. Dashboard restart ~90 s cho'zilishi mumkin (GPS threadlari) —
   "hali ko'tarilmadi" deb ikkinchi marta restart qilmang.

Bog'liq: [[Ishga-tushirish-va-Deploy]], [[Tekshirish-qoidalari]].

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Ishga-tushirish-va-Deploy]]
- [[Xato-jurnali]]
- [[profiles-ruxsat]]
- [[Tekshirish-qoidalari]]

<!-- avto-bog'liq:end -->
