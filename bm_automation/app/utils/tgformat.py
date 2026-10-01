"""Telegram HTML matn formatlash yordamchilari ("Jo'nash taxtasi" uslubi).

Ranglar emoji-semantikaga tarjima qilinadi: ✅ (yaxshi / >=95%),
⚠️ (e'tibor / 80-94%), ❌ (muammo / <80%). Barcha jadvallar `<pre>`
bloki ichida mono-shrift bilan tekislanadi.

Telegram qoidasi: `<pre>` ichida `<`, `>`, `&` albatta `html.escape`
qilinadi va HTML teglar ishlamaydi (faqat `&lt;b&gt;` ko'rinadi). Shu
sababli `badge()` (HTML, matndan tashqari) va `badge_plain()` (tegsiz,
`<pre>` ichi uchun) alohida.
"""

from __future__ import annotations

import html

__all__ = ["CHECK", "WARN", "BAD", "esc", "fmt", "pct", "badge",
           "badge_plain", "table"]

CHECK = "✅"
WARN = "⚠️"
BAD = "❌"


def esc(value) -> str:
    """Matnni HTML xavfsiz qiladi (`<`, `>`, `&` — `<pre>` ichiga qo'yish uchun)."""
    return html.escape(str(value or ""), quote=False)


def fmt(value, nd: int = 1) -> str:
    """Raqamni chiroyli formatlaydi (3.0 -> 3, 1234.56 -> 1 234.6)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "-"
    if abs(f - round(f)) < 0.05:
        return f"{int(round(f)):,}".replace(",", " ")
    return f"{f:,.{nd}f}".replace(",", " ")


def pct(value):
    """Foizni float'ga aylantiradi; imkonsiz bo'lsa None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _icon(value) -> str:
    p = pct(value)
    if p is None:
        return "-"
    if p >= 95:
        return CHECK
    if p >= 80:
        return WARN
    return BAD


def badge(value) -> str:
    """Matndan tashqarida foiz belgisi: `✅ <b>98%</b>`."""
    p = pct(value)
    if p is None:
        return "-"
    return f"{_icon(p)} <b>{p:g}%</b>"


def badge_plain(value) -> str:
    """`<pre>` ichidagi foiz belgisi (HTML tagsiz): `✅ 98%`."""
    p = pct(value)
    if p is None:
        return "-"
    return f"{_icon(p)} {p:g}%"


def table(headers: list[str], rows: list[list[str]], max_width: int = 46) -> str:
    """Monospace HTML-jadval: `<pre>` ichida box-drawing, hizalandi.

    Barcha katakchalar escape qilinadi. Jami kenglik `max_width` dan oshsa
    o'ng ustunlar avval qisqartiriladi (oxiriga "…").
    """
    headers = [esc(h) for h in headers]
    rows = [[esc(c) for c in r] for r in rows]

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    n = len(widths)
    total = sum(w + 2 for w in widths) + n + 1
    deficit = total - max_width
    if deficit > 0:
        # Har bir ustun o'z sarlavhasigacha (yoki kamida 3 belgigacha)
        # qisqarishi mumkin. Kesish eng ko'p "bo'shliq"qa ega (ya'ni eng
        # keng, odatda erkin matnli) ustunga tushadi — shunda tor ustunlar
        # (sana, holat, raqam) butun qoladi, avvalgiday o'ng ustunlar
        # birinchi bo'lib "…"gacha maydalanmaydi.
        floors = [min(w, max(len(headers[i]), 3)) for i, w in enumerate(widths)]
        while deficit > 0:
            best, best_slack = -1, 0
            for i in range(n):
                slack = widths[i] - floors[i]
                if slack > best_slack:
                    best, best_slack = i, slack
            if best < 0:
                # Hamma ustun o'z polida — baribir eng kengini qisqartiramiz.
                cand = max(range(n), key=lambda i: widths[i])
                if widths[cand] <= 1:
                    break
                widths[cand] -= 1
            else:
                widths[best] -= 1
            deficit -= 1

    def _fit(cell: str, w: int) -> str:
        if len(cell) <= w:
            return cell
        return cell[: max(w - 1, 0)] + "…"

    line = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    sep = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
    bot = "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"

    def fmt_row(cells):
        return "│" + "│".join(
            f" {_fit(cells[i], w):<{w}} " if i == 0 else f" {_fit(cells[i], w):>{w}} "
            for i, w in enumerate(widths)
        ) + "│"

    parts = [line, fmt_row(headers), sep]
    parts += [fmt_row(r) for r in rows]
    parts.append(bot)
    return "<pre>" + "\n".join(parts) + "</pre>"
