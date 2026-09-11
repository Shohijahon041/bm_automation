"""Telegram Agent — Telegram ga xabar/fayl yuboradi.

Pipeline'ning oxirgi qadami sifatida ishlaydi: tayyor hisobotni
(report), Excel faylni yoki analytics xulosasini so'ralgan chat'ga
yuboradi. chat_id params orqali (bot so'rovidan) yoki default
TG_CHAT_ID dan olinadi.
"""

from __future__ import annotations

from ..utils.tgformat import esc as _escape
from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.telegram_agent")


def _plain_to_html(text: str) -> str:
    """Oddiy matnni Telegram HTML parse_mode uchun xavfsiz qiladi."""
    if not text:
        return text
    text = _escape(text)
    # **bold** → <b>bold</b> (agar LLM markdown qaytarsa)
    import re
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return text


def _build_summary(ana: dict) -> str:
    """Analytics natijasidan qisqa, insoniy xulosa yaratadi."""
    lines: list[str] = []
    if ana.get("total_trips"):
        lines.append(
            f"🚏 Qatnovlar: jami {ana.get('total_trips')} — "
            f"qabul qilingan: {ana.get('accepted', 0)}, "
            f"qabul qilinmagan: {ana.get('not_accepted', 0)}")
        if ana.get("total_vehicles"):
            lines.append(f"🚌 Avtobuslar: {ana['total_vehicles']} ta")
        lines.append(
            f"Bajarilish: {ana.get('completion_rate', 0)}% | "
            f"Umumiy masofa: {ana.get('total_km', 0)} km")
    if ana.get("attendance_data"):
        att = ana["attendance_data"]
        lines.append(
            f"✅ Davomat: {att.get('present', 0)}/"
            f"{att.get('scheduled_count', att.get('present', 0))} "
            f"({att.get('attendance_rate', 0)}%)")
    if ana.get("route_stats"):
        lines.append("Yo'nalishlar bo'yicha:")
        for r, st in ana["route_stats"].items():
            lines.append(
                f"  • {r}: {st['trips']} qatnov, "
                f"{st.get('vehicles', 0)} avtobus, {st['km']:.1f} km")
    if not lines:
        keys = [k for k, v in ana.items() if k not in ("error", ) and v]
        lines.append("Analytics: " + ", ".join(keys[:4]) if keys
                     else "Analytics bo'sh natija qaytardi.")
    return "\n".join(lines)


def _build_monthly_message(drv: dict) -> str:
    """Oylik haydovchi payload'idan qisqa xulosa (Telegram uchun)."""
    if not isinstance(drv, dict):
        return ""
    period = drv.get("period", "")
    lines: list[str] = []
    if "drivers" in drv:
        lines.append(f"📅 <b>OYLIK HAYDOVCHILAR</b> · {period}")
        totals = drv.get("totals") or {}
        lines.append(
            f"👥 Haydovchilar: {totals.get('active_drivers', 0)}/{totals.get('drivers', 0)} "
            f"faol · qatnovlar: {totals.get('total_trips', 0)}")
        lines.append(
            f"📏 Masofa: {totals.get('total_km', 0)} km · "
            f"maosh (net): {totals.get('total_net', 0):,}".replace(",", " "))
        lst = drv.get("drivers") or []
        lines.append("")
        lines.append("🏆 Eng yaxshilari:")
        for r in lst[:5]:
            lines.append(
                f"  • {_escape(r.get('name', '-'))} — "
                f"{r.get('km', 0)} km, {r.get('trips', 0)} qatnov, "
                f"net: {r.get('net_pay', 0):,}".replace(",", " "))
    elif drv.get("driver") and isinstance(drv.get("driver"), dict):
        d = drv["driver"]
        lines.append(f"👤 <b>{_escape(d.get('name', '-'))}</b> · {period}")
        lines.append(
            f"📏 {d.get('km', 0)} km · {d.get('trips', 0) + d.get('manual_trips', 0)} "
            f"qatnov · {d.get('working_days', 0)} ish kuni")
        lines.append(
            f"💰 Maosh: gross {d.get('gross_pay', 0)}, "
            f"net {d.get('net_pay', 0)} · soliq {d.get('tax', 0)} · "
            f"jarima {d.get('fines', 0)}")
        lines.append(f"⭐ Baho: {d.get('rating', 0)}")
    return "\n".join(lines)


class TelegramAgent(BaseAgent):
    name = AgentType.TELEGRAM
    description = "Telegram ga xabar yuboradi"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start(task_id=context.task_id)
        try:
            prev = context.previous_results or {}
            params = context.params or {}

            chat_id = str(params.get("chat_id") or "").strip()

            # 1) Excel fayl tayyor bo'lsa — faylni yuboramiz
            excel = prev.get("excel") or {}
            if excel.get("path"):
                caption = _plain_to_html(
                    params.get("caption") or "📊 Excel hisobot tayyor")
                await self._use_tool(
                    "telegram", action="send_document",
                    path=excel["path"], chat_id=chat_id,
                    caption=caption,
                )
                self.source_note = "Telegram messenjer"
                self._finish(True, "Excel fayl yuborildi",
                             task_id=context.task_id)
                return AgentResult(success=True, data={
                    "sent": True, "method": "document",
                    "chat_id": chat_id, "message": caption,
                    "path": excel["path"],
                })

            # 2) Matn: report agent bo'lsa — uning hisobotini yuboramiz
            message = ""
            report = prev.get("report") or {}
            if report.get("report"):
                message = report["report"]
            elif params.get("message"):
                message = str(params["message"])

            # 3) Report bo'lmasa — analytics xulosasini yuboramiz
            if not message:
                ana = prev.get("analytics") or {}
                if isinstance(ana, dict) and ana:
                    message = _build_summary(ana)

            # 4) Oylik haydovchi payload bo'lsa — qisqa oylik xulosa
            if not message:
                drv = prev.get("driver") or {}
                if isinstance(drv, dict) and drv.get("period"):
                    message = _build_monthly_message(drv)

            if not message:
                message = "✅ Topshiriq bajarildi. Telegram orqali yuborish uchun ma'lumot topilmadi."

            await self._use_tool(
                "telegram", action="send_message",
                chat_id=chat_id, text=_plain_to_html(message),
            )
            self.source_note = "Telegram messenjer"
            self._finish(True, "Xabar yuborildi", task_id=context.task_id)
            return AgentResult(success=True, data={
                "sent": True, "method": "message",
                "chat_id": chat_id, "message": message,
            })
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc), task_id=context.task_id)
            return AgentResult(success=False, error=str(exc))