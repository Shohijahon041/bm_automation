"""Haydovchilar kunlik jadvali PNG-rasmini yaratish (matplotlib).

Bu modul faqat chizish bilan shug'ullanadi — ma'lumot yig'ish (duty, waybill,
yo'nalishlar) `app/services/driver_sheet_service` da amalga oshiriladi.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from ..utils.text import title_case

__all__ = ["make_sheet_image", "make_personal_sheet_image"]

# Chizma ranglari
BG = "#F4F6FB"
TITLE_BG = "#1E3A5F"
PANEL = {
    "UP": {"header": "#2E6FBF", "band": "#D6E4F5", "accent": "#2E6FBF", "row": "#F2F7FC", "badge": "#D6E4F5"},
    "DOWN": {"header": "#2F8F6B", "band": "#D8EEE3", "accent": "#2F8F6B", "row": "#F0F8F4", "badge": "#D8EEE3"},
}
TEXT_MAIN = "#1B2A3A"
TEXT_MUTED = "#5A6B7C"
TEXT_WHITE = "#FFFFFF"
GRID = "#C7D2E0"

_WEEKDAY_UZ = {
    0: "Dushanba", 1: "Seshanba", 2: "Chorshanba", 3: "Payshanba",
    4: "Juma", 5: "Shanba", 6: "Yakshanba",
}


def _draw_rounded(ax, x, y, w, h, fc, ec="none", r=0.14, lw=0):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={r}",
        facecolor=fc, edgecolor=ec, linewidth=lw,
    )
    ax.add_patch(patch)
    return patch


def _draw_badge(ax, x, y, w, h, text, fc, tc, fs=12):
    _draw_rounded(ax, x, y, w, h, fc, r=min(h, 0.5) / 2)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc, fontweight="bold")


def _draw_panel(ax, x, y_top, width, direction, title, rows, theme, row_h=0.92, scale=1.0):
    def F(v):
        return v * scale

    # Sarlavha bandi
    hdr_h = 0.62 * max(0.8, scale)
    _draw_rounded(ax, x, y_top - hdr_h, width, hdr_h, theme["header"], r=0.18)
    label = "CHIQISH 1" if direction == "UP" else "CHIQISH 2"
    ax.text(x + 0.22, y_top - hdr_h / 2, label, ha="left", va="center", fontsize=F(13),
            color=TEXT_WHITE, fontweight="bold")
    ax.text(x + width - 0.22, y_top - hdr_h / 2, title_case(title), ha="right", va="center",
            fontsize=F(16), color=TEXT_WHITE, fontweight="bold")

    y = y_top - hdr_h
    # Ustun sarlavhalari
    ch = 0.34 * max(0.8, scale)
    cols = [("GRAFIK", 0.9), ("HAYDOVCHI", 3.3), ("AVTOBUS", 1.6), ("CHIQISH", 1.1)]
    _draw_rounded(ax, x, y - ch, width, ch, theme["band"], r=0.08)
    cx = x + 0.18
    for name, cw in cols:
        ax.text(cx + cw / 2, y - ch / 2, name, ha="center", va="center", fontsize=F(11),
                color=TEXT_MUTED, fontweight="bold")
        cx += cw

    y -= ch
    # Qatorlar
    for i, r in enumerate(rows):
        fc = theme["row"] if i % 2 == 0 else "#FFFFFF"
        _draw_rounded(ax, x, y - row_h, width, row_h, fc, ec=GRID, lw=0.6, r=0.1)
        ax.add_patch(FancyBboxPatch((x, y - row_h), 0.09, row_h,
                                    boxstyle="round,pad=0,rounding_size=0.01",
                                    facecolor=theme["accent"], edgecolor="none"))
        cx = x + 0.18
        # Grafik
        ax.text(cx + 0.45, y - row_h / 2, r["graph"], ha="center", va="center", fontsize=F(15),
                color=theme["accent"], fontweight="bold")
        cx += cols[0][1]
        # Haydovchi
        ax.text(cx + 0.12, y - row_h / 2, title_case(r["driver"]), ha="left", va="center",
                fontsize=F(12.5), color=TEXT_MAIN, fontweight="bold")
        cx += cols[1][1]
        # Avtobus
        bw, bh = 1.3 * max(0.85, scale), 0.42 * scale
        _draw_badge(ax, cx + (cols[2][1] - bw) / 2, y - row_h / 2 - bh / 2,
                    bw, bh, r["bus"], theme["badge"], theme["accent"], fs=F(13))
        cx += cols[2][1]
        # Chiqish vaqti
        ax.text(cx + cols[3][1] / 2, y - row_h / 2 + 0.13 * scale, r["start"][:5], ha="center",
                va="center", fontsize=F(17), color=TEXT_MAIN, fontweight="bold")
        if r["end"] and row_h >= 0.7:
            ax.text(cx + cols[3][1] / 2, y - row_h / 2 - 0.2 * scale, r["end"][:5], ha="center",
                    va="center", fontsize=F(10), color=TEXT_MUTED)
        y -= row_h
    return y


def make_sheet_image(data: dict, out_file: str, width_in: float = 15.0) -> str:
    """Jadval ma'lumotidan rangli PNG rasm yaratadi.

    Qatorlar soniga moslashadi: 12+ qator bo'lsa qator balandligi va
    shriftlar kichiklashadi, shunda 16 qatorli grafik ham chiroyli
    va ko'rish mumkin bo'lgan rasm bo'lib qoladi.
    """
    groups = data["groups"]
    n_rows = max(len(groups["UP"]), len(groups["DOWN"]), 1)

    # Qator balandligini moslash: ko'p qator bo'lsa rasmni haddan tashqari
    # cho'zmaslik uchun kichraytiramiz.
    max_height_in = 14.0          # rasm balandligi yuqori chegarasi
    title_h = 1.0                 # yuqori sarlavha zonasi
    footer_h = 0.8                # pastki izoh zonasi
    row_h = min(0.92, (max_height_in - title_h - footer_h - 1.11) / n_rows)
    row_h = max(row_h, 0.52)
    scale = max(0.62, min(1.0, row_h / 0.92))

    hdr_h = 0.62 * max(0.8, scale)
    ch = 0.34 * max(0.8, scale)
    # Panel y_top = footer + panel balandligi — shunda 16+ qator ham
    # kesilmasdan joylashadi.
    panel_h = hdr_h + ch + n_rows * row_h + 0.15
    height_in = footer_h + panel_h + title_h
    top = footer_h + panel_h

    fig = Figure(figsize=(width_in, height_in), dpi=140)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width_in)
    ax.set_ylim(0, height_in)
    ax.axis("off")
    ax.set_facecolor(BG)
    fig.patch.set_facecolor(BG)

    # Sarlavha
    d = date.fromisoformat(data["date"])
    weekday_uz = _WEEKDAY_UZ[d.weekday()]
    title = f"{data['routeName']}  |  {d:%d.%m.%Y}  ({weekday_uz})"
    sub = "Kunlik chiqish jadvali"
    _draw_rounded(ax, 0.25, height_in - 0.82, width_in - 0.5, 0.72, TITLE_BG, r=0.2)
    ax.text(0.55, height_in - 0.55, title, ha="left", va="center", fontsize=21,
            color=TEXT_WHITE, fontweight="bold")
    ax.text(width_in - 0.55, height_in - 0.55, sub, ha="right", va="center", fontsize=13,
            color="#AFC3DC")

    # Ikki panel
    pad = 0.3
    half = (width_in - 0.5 - pad) / 2
    x1 = 0.25
    x2 = x1 + half + pad
    for d_, x, t in (("UP", x1, data["konechka"]["UP"]), ("DOWN", x2, data["konechka"]["DOWN"])):
        _draw_panel(ax, x, top, half, d_, t, groups[d_], PANEL[d_], row_h=row_h, scale=scale)

    # Pastki qismda konechka tushuntirishi
    ax.text(0.25, 0.35, f"1-chiqish - {title_case(data['konechka']['UP'])}", ha="left",
            va="center", fontsize=11, color=TEXT_MUTED)
    ax.text(width_in - 0.25, 0.35, f"2-chiqish - {title_case(data['konechka']['DOWN'])}",
            ha="right", va="center", fontsize=11, color=TEXT_MUTED)

    fig.savefig(out_file, bbox_inches="tight", facecolor=BG)
    fig.clear()
    return str(Path(out_file))


def _draw_rounded_photo(ax, x, y, size, img_path: str) -> bool:
    """Yumaloq burchakli doira ichida haydovchi rasmini chizadi.

    Rasm bo'lmasa False — chaqiruvchi bosh harf belgisi chizadi.
    """
    try:
        from matplotlib.image import imread
        img = imread(img_path)
    except Exception:  # noqa: BLE001 - rasm yo'q/ buzuk
        return False
    try:
        # Doira maska shakli (clip path)
        from matplotlib.patches import Circle
        cx, cy = x + size / 2, y + size / 2
        circ = Circle((cx, cy), size / 2, transform=ax.transData)
        ax.imshow(img, extent=(x, x + size, y, y + size), zorder=3,
                  clip_path=circ, clip_on=True, aspect="auto")
        # Chiroyli oq ramka
        ax.add_patch(Circle((cx, cy), size / 2, fill=False,
                            edgecolor="#FFFFFF", linewidth=2.5, zorder=4))
        return True
    except Exception:  # noqa: BLE001
        return False


def make_personal_sheet_image(row: dict, out_file: str, date_str: str = "",
                              route_name: str = "", photo_path: str = "",
                              km_stats: dict | None = None) -> str:
    """Bitta haydovchining SHAXSIY grafik kartasi (rasmi).

    Faqat o'z grafigi, ismi, avtobusi va chiqish vaqti ko'rinadi —
    jamoa jadvali emas. Haydovchi botdagi "Grafikim" tugmasi orqali
    shu rasmni oladi.

    photo_path — haydovchi rasmi (doira ichida chiziladi, bo'lmasa
    ism bosh harfi). km_stats — {"plan_km", "fact_km", "plan_trips",
    "fact_trips"} — oy bo'yicha reja/amalda statistikasi.
    """
    d = date.fromisoformat(date_str) if date_str else date.today()
    weekday_uz = _WEEKDAY_UZ[d.weekday()]

    H = 6.4 if (photo_path or km_stats) else 4.6
    W = 7.2
    fig = Figure(figsize=(W, H), dpi=140)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    ax.set_facecolor(BG)
    fig.patch.set_facecolor(BG)

    # Sarlavha
    _draw_rounded(ax, 0.3, H - 1.05, W - 0.6, 0.8, TITLE_BG, r=0.2)
    ax.text(0.55, H - 0.52, "SIZNING GRAFIKINGIZ", ha="left", va="center",
            fontsize=15, color=TEXT_WHITE, fontweight="bold")
    ax.text(W - 0.55, H - 0.52, f"{d:%d.%m.%Y} · {weekday_uz}", ha="right",
            va="center", fontsize=12, color="#AFC3DC")

    # Haydovchi rasmi yoki bosh harf (chap tomonda, yumaloq doira)
    photo_size = 1.25
    photo_x, photo_y = 0.6, H - 2.05 - photo_size / 2
    photo_drawn = _draw_rounded_photo(ax, photo_x, photo_y, photo_size,
                                      photo_path) if photo_path else False
    if not photo_drawn:
        # Bosh harf bilan avatar
        initial = title_case(str(row.get("driver") or "H"))[:1].upper() or "H"
        _draw_rounded(ax, photo_x, photo_y, photo_size, photo_size,
                      "#2E6FBF", r=photo_size / 2)
        ax.text(photo_x + photo_size / 2, photo_y + photo_size / 2, initial,
                ha="center", va="center", fontsize=30, color=TEXT_WHITE,
                fontweight="bold")

    # Ism + yo'nalish (rasmdan keyin)
    name_x = photo_x + photo_size + 0.35
    ax.text(name_x, photo_y + photo_size - 0.25,
            title_case(str(row.get("driver") or "Haydovchi")),
            ha="left", va="center", fontsize=17, color=TEXT_MAIN,
            fontweight="bold")
    if route_name:
        ax.text(name_x, photo_y + photo_size - 0.7, route_name,
                ha="left", va="center", fontsize=12, color=TEXT_MUTED)

    # Ma'lumot kartalari: Grafik | Avtobus | Chiqish | Tugash
    cards = [
        ("GRAFIK", str(row.get("graph") or "-"), "#2E6FBF"),
        ("AVTOBUS", str(row.get("bus") or "-"), "#2F8F6B"),
        ("CHIQISH", str(row.get("start") or "-")[:5], "#C96A1E"),
        ("TUGASH", str(row.get("end") or "-")[:5], "#8E44AD"),
    ]
    cw = (W - 0.6 - 0.45) / 4
    ch_h = 1.25
    cy = H - 4.35
    for i, (label, value, color) in enumerate(cards):
        cx = 0.3 + i * (cw + 0.15)
        _draw_rounded(ax, cx, cy, cw, ch_h, "#FFFFFF", ec=GRID, lw=0.8, r=0.16)
        _draw_rounded(ax, cx, cy + ch_h - 0.34, cw, 0.34, color, r=0.1)
        ax.text(cx + cw / 2, cy + ch_h - 0.17, label, ha="center", va="center",
                fontsize=10.5, color=TEXT_WHITE, fontweight="bold")
        ax.text(cx + cw / 2, cy + (ch_h - 0.34) / 2, value, ha="center",
                va="center", fontsize=20, color=TEXT_MAIN, fontweight="bold")

    # Tugash vaqti — kichik izoh
    shift = str(row.get("shift") or "").strip()
    if shift:
        ax.text(0.6, cy - 0.42, f"Smena: {shift}", ha="left",
                va="center", fontsize=12, color=TEXT_MUTED)

    # Reja/amalda km statistikasi (oy bo'yicha) — pastki panel
    if km_stats:
        plan_km = float(km_stats.get("plan_km") or 0)
        fact_km = float(km_stats.get("fact_km") or 0)
        plan_tr = int(km_stats.get("plan_trips") or 0)
        fact_tr = int(km_stats.get("fact_trips") or 0)
        pct = (fact_km / plan_km * 100) if plan_km > 0 else 0.0
        pct = max(0.0, min(pct, 150.0))
        bar_y = 0.95
        _draw_rounded(ax, 0.3, bar_y - 0.42, W - 0.6, 1.25, "#FFFFFF",
                      ec=GRID, lw=0.8, r=0.16)
        ax.text(0.55, bar_y + 0.52, "OY BO'YICHA NATIJA", ha="left",
                va="center", fontsize=10, color=TEXT_MUTED,
                fontweight="bold")
        # Qatnov soni — katta raqam (markazda, o'ng tomonda)
        ax.text(W - 0.7, bar_y + 0.5, f"Qatnov: {fact_tr}",
                ha="right", va="center", fontsize=16, color=TEXT_MAIN,
                fontweight="bold")
        # Progress bar (fon + to'ldirilgan qism)
        bar_w = W - 3.3
        _draw_rounded(ax, 0.55, bar_y - 0.02, bar_w, 0.3, "#E4EAF2", r=0.15)
        fill_w = bar_w * pct / 100
        if fill_w > 0.05:
            bar_color = "#2F8F6B" if pct >= 80 else ("#C9A21E" if pct >= 50 else "#C94A3A")
            _draw_rounded(ax, 0.55, bar_y - 0.02, fill_w, 0.3, bar_color, r=0.15)
        ax.text(0.55 + bar_w + 0.15, bar_y + 0.13, f"{pct:.0f}%",
                ha="left", va="center", fontsize=13, color=TEXT_MAIN,
                fontweight="bold")
        # Raqamlar satrı
        ax.text(0.55, bar_y - 0.28,
                f"Reja km: {plan_km:,.0f}   ·   Amalda: {fact_km:,.0f} km",
                ha="left", va="center", fontsize=11, color=TEXT_MUTED)
        ax.text(W - 0.7, bar_y - 0.28,
                f"Reja reys: {plan_tr}",
                ha="right", va="center", fontsize=11, color=TEXT_MUTED)

    fig.savefig(out_file, bbox_inches="tight", facecolor=BG)
    fig.clear()
    return str(Path(out_file))
