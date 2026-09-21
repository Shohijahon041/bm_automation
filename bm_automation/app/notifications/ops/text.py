"""Matnlar: yordam, asosiy menyu va umumiy sarlavhalar."""

from __future__ import annotations

HELP_TEXT = (
    "🚌 Transport Operations Bot\n\n"
    "📊 <b>Ko'rish</b>\n"
    "/today — bugungi statistika\n"
    "/month — oy tarixi (oy boshidan bugungacha)\n"
    "/routes — yo'nalishlar\n"
    "/vehicles — avtobuslar\n"
    "/vehicle — avtobus kartasi (masalan: /vehicle 01 A 235 BB)\n"
    "/drivers — haydovchilar\n"
    "/driver — haydovchi kartasi (masalan: /driver Aliyev)\n"
    "/trips — reyslar (masalan: /trips 2026-08-10 REJECTED)\n"
    "/problems — muammolar (batafsil)\n"
    "/reports — hisobotlar\n"
    "/errors — xatolar\n"
    "/status — tizim holati\n\n"
    "📊 <b>Qo'shimcha hisobotlar</b>\n"
    "/ai — AI-yordamchi (tabiiy tilda so'rang, masalan: /ai davomat qanday?)\n"
    "/top — haydovchilar reytingi (/top km|trips|net|attendance)\n"
    "/distance — avtobuslar masofa (km) hisoboti\n"
    "/schedule — yo'nalish jadvali (/schedule 2026-08-15)\n"
    "/attendance — davomat (/attendance 2026-08-15)\n"
    "/alerts — muammo alertlari holati\n"
    "/insights — AI o'z-o'zini rivojlantirish tahlili (loglar + tavsiyalar)\n\n"
    "📈 <b>Amallar</b>\n"
    "/export — eksport (xlsx|csv|pdf) [scope]\n"
    "/plan — ertangi reja (haydovchi/grafik qo'shish)\n"
    "/brutto — brutto-shartnoma to'lovi (116-son qaror, salary)\n"
    "/hisob — haydovchi oylik hisobi (salary)\n"
    "/sync — sinxronlash (kunlik)\n"
    "/syncmonthly — oylik sinxronlash (oy boshidan bugunga)\n"
    "/addcompany — yangi firma ro'yxatdan o'tkazish (ADMIN)\n"
    "/login — firma egasi o'z login/parolini kiritadi\n\n"
    "⚙️ <b>Boshqa</b>\n"
    "/start — asosiy menyu\n"
    "/resend — profillarni qayta yuborish (ADMIN/DISPATCHER)\n"
    "/list — so'nggi fayllar\n"
    "/help — yordam\n"
    "/myrole — mening rol (huquq darajasi)\n"
    "/users — bot foydalanuvchilari ro'yxati (ADMIN/DISPATCHER)\n"
    "/setrole &lt;id&gt; &lt;rol&gt; — foydalanuvchi rolini tayinlash (ADMIN)\n"
    "/sohbat — MyAI bilan uzluksiz suhbat rejimi (qayta bosish = o'chirish)\n\n"
    "Excel (.xlsx) yuborsangiz — sayt export formatida jadval to'ldiriladi.\n"
    "Huquqlar: ADMIN (hammasi) / DISPATCHER (eksport) / VIEWER (ko'rish).\n"
    "🏢 <b>Ko'p-firmali rejim:</b> har firma egasi faqat o'z firmasini ko'radi; "
    "ADMIN barchasini."
)

WELCOME_TEXT = (
    "🚌 <b>Transport Operations Bot</b>\n\n"
    "Avtobus parki, reyslar va muammolar haqida jonli ma'lumot.\n\n"
    "🤖 <b>AI-yordamchi:</b> tabiiy tilda so'rang — masalan "
    "\"Xulosa ber\", \"Davomat qanday?\", \"Ertaga qanday bo'ladi?\".\n\n"
    "Bugungi statistika uchun /today bosing yoki quyidagi menyudan tanlang."
)

DENIED_TEXT = "⛔ Sizda bu amal uchun huquq yo'q."

PRIVATE_TEXT = (
    "⛔ <b>Bu bot shaxsiy foydalanish uchun.</b>\n"
    "Sizning chat ID'ingiz ruxsat etilganlar ro'yxatida yo'q."
)

SYNC_STARTED = "🔄 Sinxronlash boshlandi... tugagach natijani yuboramiz."
SYNC_MONTHLY_STARTED = "🔄 Oylik sinxronlash boshlandi... tugagach natijani yuboramiz."
