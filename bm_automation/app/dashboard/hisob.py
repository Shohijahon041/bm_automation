"""116-son (18.03.2023) brutto-shartnoma hisob-kitob yadrosi.

Vazirlar Mahkamasi 116-son qarori (NIZOM 2-ilova + HISOB-KITOB ilovalari)
bo'yicha:
  - shahar avtobus yo'nalishida 1 km narx = xarajat bloklari yig'indisi
    (yoqilg'i-moylash, haydovchi mehnatiga haq, amortizatsiya, sug'urta,
    infratuzilma) / yillik masofa;
  - tasdiqlanadigan 1 km narx ana shu bloklar asosida aniqlanadi;
  - haydovchi brutto ish haqi = per-route km × haydovchi 1-km mehnat stavkasi
    (4-ILOVA HISOB-KITOB), sifat bo'yicha ustama + brutto,
    so'ng soliq (12%), jarima va netto aniqlanadi (116-son 2-bob printsipi:
    brutto-shartnoma asosida mehnat haqini shakllantirish).

Bu modul faqat HISOB-KITOB (hisob) qatlami — statistikani `metrics` dan,
sozlamalarni `bot_settings` dan oladi va Excel'ga (export') mos natija qaytaradi.
"""

from __future__ import annotations

from datetime import date

# ---- Qonuniy (116-son) doimiy parametrlari ----
DEFAULT_TAX_RATE = 0.12          # oylik brutto ish haqidan soliq (12%)
DEFAULT_FUEL_PER_KM = 940.0      # yoqilg'i-moylash xarajati (1-km, so'm)
DEFAULT_LABOR_PER_KM = 0.0       # haydovchi mehnatiga 1-km (km_rate dan)
DEFAULT_AMORT_PER_KM = 67.0      # amortizatsiya 1-km (so'm)
DEFAULT_INSUR_PER_KM = 98.0      # sug'urta 1-km (so'm)
DEFAULT_INFRA_PER_KM = 389.0     # infratuzilma xarajati 1-km (so'm)
DEFAULT_ANNUAL_KM = 85400.0      # yo'nalish bo'yicha yillik masofa (km)


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def km_rate_for(route_id, base: float = 0.0) -> float:
    """Haydovchi 1-km mehnat stavkasi (116-son 4-ILOVA).

    `metrics.km_rate_for` qonuniy stavkani (route tarif → % mehnat) qaytaradi.
    Shu yerda faqat asosiy fallback: bot_settings tarifi.
    """
    try:
        from ..core.bot_settings import km_rate_for as _real
        return _real(route_id, base)
    except Exception:  # noqa: BLE001 - modul alohida test uchun
        return base


def cost_blocks(route_id: str | None = None) -> dict:
    """116-son NIZOM bo'yicha 1-km xarajat bloklari.

    Bloklar `bot_settings.route_cost_blocks` dan olinadi (mavjud bo'lsa),
    aks holda 116-son doimiy standartlari ishlatiladi.
    """
    try:
        from ..core.bot_settings import route_cost_blocks
        blk = route_cost_blocks(str(route_id or ""))
        if blk:
            return blk
    except Exception:  # noqa: BLE001
        pass
    return {
        "fuel_per_km": DEFAULT_FUEL_PER_KM,
        "labor_per_km": DEFAULT_LABOR_PER_KM,
        "amort_per_km": DEFAULT_AMORT_PER_KM,
        "insur_per_km": DEFAULT_INSUR_PER_KM,
        "infra_per_km": DEFAULT_INFRA_PER_KM,
        "annual_km": DEFAULT_ANNUAL_KM,
    }


def tariff_1km(route_id: str | None = None) -> float:
    """Tasdiqlanadigan 1-km narx (116-son NIZOM): Σ bloklar ÷ yillik masofa."""
    blk = cost_blocks(route_id)
    annual = _num(blk.get("annual_km"), DEFAULT_ANNUAL_KM) or 1.0
    total = sum(_num(blk.get(k)) for k in (
        "fuel_per_km", "labor_per_km", "amort_per_km",
        "insur_per_km", "infra_per_km"))
    return round(total / annual, 6)


def _route_salary(f: dict | None, driver_id: str) -> dict:
    """Haydovchi brutto/netto (per-route) — `metrics` real hisobidan foydalanadi.

    Qaytaradi: {gross_pay, tax, fines, net_pay, km, km_rate, route_id,
                quality_bonus, gross_full}
    """
    try:
        from .metrics import Metrics
        met = Metrics()
        row = met.driver_card(driver_id, f)
        gross = _num(row.get("gross_pay"))
        tax = _num(row.get("tax"))
        fine = _num(row.get("fines"))
        net = _num(row.get("net_pay"))
        km = _num(row.get("km"))
        rate = _num(row.get("km_rate"))
        rid = str(row.get("route_id") or "")
    except Exception:  # noqa: BLE001 - metrics bo'lmasa ichki oddiy hisob
        gross = tax = fine = net = km = 0.0
        rate = 0.0
        rid = ""
    return {
        "gross_pay": gross,
        "tax": tax,
        "fines": fine,
        "net_pay": net,
        "km": km,
        "km_rate": rate,
        "route_id": rid,
        "quality_bonus": 0.0,
        "gross_full": gross,
    }


def brutto(filters: dict | None = None, driver_id: str = "") -> dict:
    """To'liq brutto/netto hisob-kitob qatori (116-son).

    `quality_bonus`: sifat bo'yicha ustama — haydovchi oylik reytingi
    (rating ≥ 4.5 → +5%, 5.0 → +10%) Mehnat kodeksi 116-sonning
    brutto-shartnoma sifat ustamasiga mos.
    zero bo'lsa — ustama qo'shilmaydi (km × km_rate yetarli).
    """
    row = _route_salary(filters, driver_id)
    rating = 5.0
    try:
        from .metrics import Metrics
        rating = _num(Metrics().driver_card(driver_id, filters).get("rating") or 5, 5)
    except Exception:  # noqa: BLE001
        pass
    gross = _num(row.get("gross_pay"))
    if rating >= 5.0:
        quality = 0.10
    elif rating >= 4.5:
        quality = 0.05
    else:
        quality = 0.0
    bonus = round(gross * quality, 2)
    gross_full = round(gross + bonus, 2)
    tax = round(gross_full * DEFAULT_TAX_RATE, 2)
    fine = _num(row.get("fines"))
    net = round(max(gross_full - tax - fine, 0), 2)
    return {
        "gross_pay": gross,
        "quality_rate": quality,
        "quality_bonus": bonus,
        "gross_full": gross_full,
        "tax": tax,
        "fines": fine,
        "net_pay": net,
        "km": _num(row.get("km")),
        "km_rate": _num(row.get("km_rate")),
        "route_id": row.get("route_id") or "",
    }


def hisob_text(filters: dict | None = None, driver_id: str = "") -> tuple[str, dict]:
    """Bot ekrani uchun HISOB-KITOB matni: brutto (hajm+sifat) → soliq → netto.

    Qaytaradi: (matn, {}) — inline tugma bo'lmasa, `kb.none()` uchun {}.
    """
    h = brutto(filters, driver_id)
    rid = h.get("route_id") or ""
    t1 = tariff_1km(rid)
    blk = cost_blocks(rid)

    def fnum(v):
        try:
            return f"{float(v or 0):,.2f}".replace(",", " ")
        except (TypeError, ValueError):
            return "0"

    lines = [
        "? <b>HISOB-KITOB (116-son)</b>",
        "",
        "? <b>1 KM NARXI (tasdiqlanadi)</b>",
        f"  Yoqilg'i-moylash: {fnum(blk.get('fuel_per_km'))} so'm",
        f"  Haydovchi mehnat: {fnum(blk.get('labor_per_km'))} so'm",
        f"  Amortizatsiya: {fnum(blk.get('amort_per_km'))} so'm",
        f"  Sug'urta: {fnum(blk.get('insur_per_km'))} so'm",
        f"  Infratuzilma: {fnum(blk.get('infra_per_km'))} so'm",
        f"  <b>1-km: {fnum(t1)} so'm</b>",
        "",
        "? <b>BRUTTO ISH HAQINGIZ</b>",
        f"  Reyslar bo'yicha (km×stavka): {fnum(h.get('gross_pay'))} so'm",
        f"  Sifat ustamasi ({int(h.get('quality_rate') * 100)}%): "
        f"{fnum(h.get('quality_bonus'))} so'm",
        f"  <b>Jami brutto: {fnum(h.get('gross_full'))} so'm</b>",
        "",
        f"  Soliq (12%): − {fnum(h.get('tax'))} so'm",
        f"  Jarimalar: − {fnum(h.get('fines'))} so'm",
        f"  <b>?? NETTO: {fnum(h.get('net_pay'))} so'm</b>",
        "",
        "Qonun asosi: VM 116-son 18.03.2023, NIZOM 2-ilova.",
    ]
    return "\n".join(lines), {}
