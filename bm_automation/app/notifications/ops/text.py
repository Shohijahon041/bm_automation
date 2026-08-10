"""Matnlar: yordam, asosiy menyu va umumiy sarlavhalar."""

from __future__ import annotations

HELP_TEXT = (
    "🚌 Transport Operations Bot\n\n"
    "📊 <b>Ko'rish</b>\n"
    "/today — bugungi statistika\n"
    "/routes — yo'nalishlar\n"
    "/vehicles — avtobuslar\n"
    "/drivers — haydovchilar\n"
    "/trips — reyslar (masalan: /trips 2026-08-10 REJECTED)\n"
    "/problems — muammolar (batafsil)\n"
    "/reports — hisobotlar\n"
    "/errors — xatolar\n"
    "/status — tizim holati\n\n"
    "📈 <b>Amallar</b>\n"
    "/export — eksport (xlsx|csv|pdf) [scope]\n"
    "/sync — sinxronlash (ADMIN)\n\n"
    "⚙️ <b>Boshqa</b>\n"
    "/start — asosiy menyu\n"
    "/resend — profillarni qayta yuborish (ADMIN)\n"
    "/list — so'nggi fayllar\n"
    "/help — yordam\n\n"
    "Excel (.xlsx) yuborsangiz — sayt export formatida jadval to'ldiriladi.\n"
    "Huquqlar: ADMIN (hammasi) / DISPATCHER (eksport) / VIEWER (ko'rish)."
)

WELCOME_TEXT = (
    "🚌 <b>Transport Operations Bot</b>\n\n"
    "Avtobus parki, reyslar va muammolar haqida jonli ma'lumot.\n\n"
    "Bugungi statistika uchun /today bosing yoki quyidagi menyudan tanlang."
)

DENIED_TEXT = "⛔ Sizda bu amal uchun huquq yo'q."

SYNC_STARTED = "🔄 Sinxronlash boshlandi... tugagach natijani yuboramiz."
