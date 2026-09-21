"""Jamoat avtobus yo'nalishlari bo'yicha brutto-shartnoma to'lov hisoblagichi.

O'zbekiston Respublikasi Vazirlar Mahkamasining 2023-yil 18-martdagi 116-son
qarori (1-ilova, 32-33-bandlar) asosida tashuvchiga to'lanadigan summani
hisoblaydi.

Asosiy formula (32-band):

    S = SKM * Lf * (1 - alpha) * (1 - beta) * (1 - gamma)

bu yerda:
    S     — hisobot davri uchun tashuvchiga to'lanadigan summa (so'm);
    SKM   — shartnomada belgilangan 1 mashina-km ishning narxi (so'm);
    Lf    — hisobot davrida amalda bosib o'tilgan masofa (km);
    alpha — "bosib o'tilgan yo'l" mezoni bo'yicha ushlab qolinadigan ulush;
    beta  — sifat mezoni bo'yicha ushlab qolinadigan ulush;
    gamma — majburiyatlarga amal qilish bo'yicha ushlab qolinadigan ulush.

To'lov muddati (33-band): to'lov hisobot oyidan keyingi oyning 15-sanasiga
qadar amalga oshiriladi.

Alohida holatlar:
- Qisqa muddatli cheklov: yo'nalish o'zgarsa, asosiy shartnomadagi masofa
  (asosiy yo'nalish Lr) bo'yicha to'lanadi.
- Uzoq muddatli cheklov: qo'shimcha shartnoma tuziladi va u bo'yicha to'lov
  amalga oshiriladi.
- Quyidagilar bajarilmagan qatnov hisoblanmaydi va to'lovda hisobga olinadi:
  YTH, tirbandlik, GPS texnik nosozlik.
- Agar Lr = 0 yoki Kamal = 0 bo'lsa, eng yuqori koeffitsiyent qo'llaniladi
  (alpha = 0.20, beta = 0.15, gamma = 0.05).

Standart kutubxonalar: dataclasses, datetime, functools.
Tashqi kutubxonalar: pandas, openpyxl (Excel import/eksport uchun).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Iterable

import pandas as pd

# --------------------------------------------------------------------------
# Koeffitsiyent jadvallari (qarorning 32-bandi ilovalari)
# --------------------------------------------------------------------------

# alpha: hajm ko'rsatkichi indeksi (Lf / Lr * 100) -> ushlab qolinadigan ulush
ALPHA_TABLE: tuple[tuple[float, float], ...] = (
    (0.0, 0.20),
    (70.0, 0.15),
    (80.0, 0.10),
    (85.0, 0.05),
    (90.0, 0.025),
    (95.0, 0.00),
)

# beta: sifat ko'rsatkichi indeksi ((Kamal - Kstjb) / Kamal * 100) -> ulush
BETA_TABLE: tuple[tuple[float, float], ...] = (
    (0.0, 0.15),
    (70.0, 0.12),
    (80.0, 0.09),
    (85.0, 0.06),
    (90.0, 0.03),
    (95.0, 0.00),
)

# gamma: majburiyat indeksi ((Kamal - Kmaq) / Kamal * 100) -> ulush
GAMMA_TABLE: tuple[tuple[float, float], ...] = (
    (0.0, 0.05),
    (70.0, 0.04),
    (80.0, 0.03),
    (85.0, 0.02),
    (90.0, 0.01),
    (95.0, 0.00),
)

# Eng yuqori (jazo) koeffitsiyentlar — Lr=0 yoki Kamal=0 holatida
MAX_ALPHA = 0.20
MAX_BETA = 0.15
MAX_GAMMA = 0.05

# Moliya bo'limi tasdiqlagan qo'shimcha stavkalar
DRIVER_RATE = 2363.8        # haydovchi 1 km ish haqi (so'm/km)
DRIVER_TAX = 0.12           # haydovchi soliq ulushi (12%)
ELECTRICITY_FACTOR = 0.955  # 1 km uchun elektr sarfi (kWt/km)
ELECTRICITY_RATE = 1100.0   # 1 kWt elektr narxi (so'm)

# Chiqish Excel jadvalidagi ustunlar (7-bo'lim)
EXCEL_COLUMNS: list[str] = [
    "Sana", "Grafik", "Davlat raqami", "F.I.O",
    "Lr (km)", "Lf (km)", "Kamal", "Kstjb", "Kmaq",
    "Hajm ind. %", "Sifat ind. %", "Majb. ind. %",
    "alpha", "beta", "gamma", "S (so'm)",
]

# Kirish (manba) DataFrame ustunlari
SOURCE_COLUMNS: list[str] = [
    "sana", "grafik", "davlat_raqami", "fio",
    "lr", "lf", "kamal", "kstjb", "kmaq",
]


def _lookup(index: float, table: tuple[tuple[float, float], ...]) -> float:
    """Jadval bo'yicha koeffitsiyentni topadi.

    - index manfiy yoki NaN bo'lsa -> eng yuqori qator (0.0);
    - index 100 dan katta bo'lsa -> eng oxirgi (past) qator (0.0).
    """
    if pd.isna(index) or index < 0:
        return table[0][1]
    result = table[0][1]
    for threshold, coef in table:
        if index >= threshold:
            result = coef
        else:
            break
    return result


def _index_100(numerator: float, denominator: float) -> float:
    """Foiz ko'rsatkich indeksi: numerator / denominator * 100.

    denominator = 0 bo'lsa 0 qaytariladi (yuqori jazo koeffitsiyenti).
    """
    try:
        if not denominator or float(denominator) <= 0:
            return 0.0
        return float(numerator) / float(denominator) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


@dataclass
class Contract:
    """Brutto-shartnoma (116-son qarori asosida).

    Attributes:
        skm: shartnomada belgilangan 1 mashina-km ishning narxi (so'm),
            misol uchun 16176.0.
        route_number: yo'nalish raqami (masalan, "B-80").
        carrier: tashuvchi (avtotransport korxonasi) nomi.
        valid_from: shartnoma kuchga kirgan sana.
        valid_to: shartnoma amal qilish muddati tugagan sana.
    """

    skm: float
    route_number: str
    carrier: str
    valid_from: date
    valid_to: date

    def is_active_on(self, day: date) -> bool:
        """Shartnoma berilgan sanada amalda bo'lganligini aniqlaydi."""
        if not day:
            return False
        if self.valid_from and day < self.valid_from:
            return False
        if self.valid_to and day > self.valid_to:
            return False
        return True


@dataclass
class TripResult:
    """Bitta qatnov (gator) bo'yicha hisob-kitob natijasi.

    Attributes:
        sana: qatnov sanasi.
        grafik: grafik (smena) nomi.
        davlat_raqami: avtobus davlat raqami.
        fio: haydovchining F.I.O.
        lr: reja bo'yicha bosib o'tilishi lozim bo'lgan masofa (km).
        lf: amalda bosib o'tilgan masofa (km).
        kamal: amalda bajarilgan qatnovlar soni.
        kstjb: sifat talabiga javob bermagan qatnovlar soni.
        kmaq: majburiyatlarga amal qilmaslik holatlari aniqlangan qatnovlar soni.
        alpha, beta, gamma: hisoblangan ushlab qolinadigan ulushlar.
        tolov: tashuvchiga to'lanadigan summa (S).
        hajm_index, sifat_index, majb_index: foiz ko'rsatkichlari.
    """

    sana: date
    grafik: str
    davlat_raqami: str
    fio: str
    lr: float
    lf: float
    kamal: int
    kstjb: int
    kmaq: int
    alpha: float
    beta: float
    gamma: float
    tolov: float
    hajm_index: float = 0.0
    sifat_index: float = 0.0
    majb_index: float = 0.0

    # Moliyaviy qo'shimcha ko'rsatkichlar (116-son qaror asosida)
    brutto_100: float = 0.0             # Lr * SKM — reja bo'yicha to'liq to'lov
    jarima: float = 0.0                 # (Lr - Lf) * SKM — bajarilmagan km jarimasi
    haydovchi_ish_haqi: float = 0.0     # Lf * DRIVER_RATE
    haydovchi_soliq: float = 0.0        # ish haqi * DRIVER_TAX
    haydovchi_qolga: float = 0.0        # ish haqi - soliq
    elektr_kwt: float = 0.0             # Lf * ELECTRICITY_FACTOR
    elektr_summ: float = 0.0            # elektr_kwt * ELECTRICITY_RATE


class BruttoCalculator:
    """116-son qarori 32-band formulasi bo'yicha to'lov hisoblagichi.

    Usage:
        contract = Contract(skm=16176.0, route_number="B-80",
                            carrier="AvtoPark", valid_from=date(2023, 4, 1),
                            valid_to=date(2025, 12, 31))
        calc = BruttoCalculator(contract)
        s = calc.calc_payment(lf=100, lr=100, kamal=100, kstjb=0, kmaq=0)
    """

    def __init__(self, contract: Contract) -> None:
        """Calculator'ni shartnoma asosida yaratadi."""
        if contract.skm <= 0:
            raise ValueError("SKM (1 km narxi) musbat bo'lishi shart.")
        self.contract = contract

    # -- Koeffitsiyentlarni hisoblash -----------------------------------

    def calc_alpha(self, lf: float, lr: float) -> float:
        """'Bosib o'tilgan yo'l' mezoni bo'yicha ushlab qolinadigan ulush.

        Hajm indeksi = (Lf / Lr) * 100%. Jadvaldan alpha aniqlanadi.
        """
        index = _index_100(lf, lr)
        return _lookup(index, ALPHA_TABLE)

    def calc_beta(self, kamal: int, kstjb: int) -> float:
        """Sifat mezoni bo'yicha ushlab qolinadigan ulush.

        Sifat indeksi = (Kamal - Kstjb) / Kamal * 100%.
        """
        index = _index_100(kamal - kstjb, kamal)
        return _lookup(index, BETA_TABLE)

    def calc_gamma(self, kamal: int, kmaq: int) -> float:
        """Majburiyatlarga amal qilish bo'yicha ushlab qolinadigan ulush.

        Majburiyat indeksi = (Kamal - Kmaq) / Kamal * 100%.
        """
        index = _index_100(kamal - kmaq, kamal)
        return _lookup(index, GAMMA_TABLE)

    # -- To'lovni hisoblash ---------------------------------------------

    def calc_payment(self, lf: float, lr: float,
                     kamal: int, kstjb: int, kmaq: int) -> float:
        """Tashuvchiga to'lanadigan summani hisoblaydi.

        S = SKM * Lf * (1 - alpha) * (1 - beta) * (1 - gamma)

        Agar Lr = 0 yoki Kamal = 0 bo'lsa, eng yuqori (jazo) koeffitsiyent
        qo'llaniladi: alpha=0.20, beta=0.15, gamma=0.05.
        """
        if not lr or not kamal:
            alpha, beta, gamma = MAX_ALPHA, MAX_BETA, MAX_GAMMA
        else:
            alpha = self.calc_alpha(lf, lr)
            beta = self.calc_beta(kamal, kstjb)
            gamma = self.calc_gamma(kamal, kmaq)
        lf_v = float(lf or 0.0)
        payment = (self.contract.skm * lf_v
                   * (1.0 - alpha) * (1.0 - beta) * (1.0 - gamma))
        return max(payment, 0.0)

    # -- Hisobotni qayta ishlash ------------------------------------------

    def process_row(self, row: dict) -> TripResult:
        """Bitta manba qatorini TripResult'ga aylantiradi.

        Qatorda `skm` ustuni bo'lsa o'sha yo'nalish SKM si ishlatiladi,
        aks holda shartnoma (`contract.skm`) dagi qiymat olinadi.
        """
        lf = float(row.get("lf") or 0.0)
        lr = float(row.get("lr") or 0.0)
        skm = float(row.get("skm") or 0.0) or self.contract.skm
        kamal = int(row.get("kamal") or 0)
        kstjb = int(row.get("kstjb") or 0)
        kmaq = int(row.get("kmaq") or 0)

        if not lr or not kamal:
            alpha, beta, gamma = MAX_ALPHA, MAX_BETA, MAX_GAMMA
            hajm_i = sifat_i = majb_i = 0.0
        else:
            alpha = self.calc_alpha(lf, lr)
            beta = self.calc_beta(kamal, kstjb)
            gamma = self.calc_gamma(kamal, kmaq)
            hajm_i = _index_100(lf, lr)
            sifat_i = _index_100(kamal - kstjb, kamal)
            majb_i = _index_100(kamal - kmaq, kamal)

        sana = row.get("sana")
        if not isinstance(sana, date):
            try:
                sana = pd.to_datetime(sana).date()
            except Exception:  # noqa: BLE001 - noto'g'ri sana bo'lsa None
                sana = sana if isinstance(sana, date) else None

        tolov = (skm * float(lf)
                 * (1.0 - alpha) * (1.0 - beta) * (1.0 - gamma))
        brutto_100 = lr * skm
        jarima = (lr - lf) * skm
        haydovchi_ish_haqi = lf * DRIVER_RATE
        haydovchi_soliq = haydovchi_ish_haqi * DRIVER_TAX
        haydovchi_qolga = haydovchi_ish_haqi - haydovchi_soliq
        elektr_kwt = lf * ELECTRICITY_FACTOR
        elektr_summ = elektr_kwt * ELECTRICITY_RATE
        return TripResult(
            sana=sana,
            grafik=str(row.get("grafik") or ""),
            davlat_raqami=str(row.get("davlat_raqami") or ""),
            fio=str(row.get("fio") or ""),
            lr=lr,
            lf=lf,
            kamal=kamal,
            kstjb=kstjb,
            kmaq=kmaq,
            alpha=alpha,
            beta=beta,
            gamma=gamma,
            tolov=max(tolov, 0.0),
            hajm_index=hajm_i,
            sifat_index=sifat_i,
            majb_index=majb_i,
            brutto_100=max(brutto_100, 0.0),
            jarima=max(jarima, 0.0),
            haydovchi_ish_haqi=max(haydovchi_ish_haqi, 0.0),
            haydovchi_soliq=max(haydovchi_soliq, 0.0),
            haydovchi_qolga=max(haydovchi_qolga, 0.0),
            elektr_kwt=max(elektr_kwt, 0.0),
            elektr_summ=max(elektr_summ, 0.0),
        )

    def process_report(self, source: pd.DataFrame) -> list[TripResult]:
        """DataFrame'dagi barcha qatorlarni qayta ishlaydi.

        Manba DataFrame quyidagi ustunlarga ega bo'lishi kerak:
        sana, grafik, davlat_raqami, fio, lr, lf, kamal, kstjb, kmaq.
        """
        results: list[TripResult] = []
        if source is None or source.empty:
            return results
        for _, row in source.iterrows():
            results.append(self.process_row(row.to_dict()))
        return results

    # -- Jami / agregat hisob ------------------------------------------

    def aggregate(self, results: Iterable[TripResult]) -> dict:
        """JAMI qatori uchun agregat ko'rsatkichlarni hisoblaydi.

        - Jami Lr, Jami Lf;
        - Jami Kamal, Kstjb, Kmaq;
        - o'rtacha alpha, beta, gamma (qatorlar bo'yicha arifmetik);
        - Jami (umumiy) to'lov: SKM * Jami_Lf * (1-avg_alpha)
            * (1-avg_beta) * (1-avg_gamma).
        """
        rows = list(results)
        n = len(rows)
        if not n:
            return {
                "lr": 0.0, "lf": 0.0,
                "kamal": 0, "kstjb": 0, "kmaq": 0,
                "alpha": 0.0, "beta": 0.0, "gamma": 0.0,
                "tolov": 0.0, "qatorlar": 0,
                "brutto_100": 0.0, "jarima": 0.0,
                "haydovchi_ish_haqi": 0.0, "haydovchi_soliq": 0.0,
                "haydovchi_qolga": 0.0,
                "elektr_kwt": 0.0, "elektr_summ": 0.0,
            }
        lr = sum(r.lr for r in rows)
        lf = sum(r.lf for r in rows)
        kamal = sum(r.kamal for r in rows)
        kstjb = sum(r.kstjb for r in rows)
        kmaq = sum(r.kmaq for r in rows)
        alpha = sum(r.alpha for r in rows) / n
        beta = sum(r.beta for r in rows) / n
        gamma = sum(r.gamma for r in rows) / n
        tolov = sum(r.tolov for r in rows)
        return {
            "lr": lr, "lf": lf,
            "kamal": kamal, "kstjb": kstjb, "kmaq": kmaq,
            "alpha": alpha, "beta": beta, "gamma": gamma,
            "tolov": max(tolov, 0.0), "qatorlar": n,
            "brutto_100": sum(r.brutto_100 for r in rows),
            "jarima": sum(r.jarima for r in rows),
            "haydovchi_ish_haqi": sum(r.haydovchi_ish_haqi for r in rows),
            "haydovchi_soliq": sum(r.haydovchi_soliq for r in rows),
            "haydovchi_qolga": sum(r.haydovchi_qolga for r in rows),
            "elektr_kwt": sum(r.elektr_kwt for r in rows),
            "elektr_summ": sum(r.elektr_summ for r in rows),
        }

    # -- Chiqishni shakllantirish ---------------------------------------

    def format_report(self, results: Iterable[TripResult]) -> pd.DataFrame:
        """Natijalarni chiqish DataFramesi'ga aylantiradi (7-bo'lim ustunlari)."""
        rows: list[list[object]] = []
        for r in results:
            rows.append([
                r.sana, r.grafik, r.davlat_raqami, r.fio,
                r.lr, r.lf, r.kamal, r.kstjb, r.kmaq,
                round(r.hajm_index, 1), round(r.sifat_index, 1),
                round(r.majb_index, 1),
                r.alpha, r.beta, r.gamma, round(r.tolov, 2),
            ])
        return pd.DataFrame(rows, columns=EXCEL_COLUMNS)

    def export_excel(self, results: Iterable[TripResult],
                     output_path: str) -> str:
        """Chiqish Excel faylini yozadi (JAMI qatori bilan).

        Args:
            results: hisob-kitob natijalari.
            output_path: saqlanadigan fayl yo'li (xlsx).

        Returns:
            Saqlangan fayl yo'li.
        """
        rows = list(results)
        df = self.format_report(rows)
        agg = self.aggregate(rows)
        jami_row = [
            "JAMI", "", "", "",
            agg["lr"], agg["lf"], agg["kamal"], agg["kstjb"], agg["kmaq"],
            "", "", "",
            agg["alpha"], agg["beta"], agg["gamma"], round(agg["tolov"], 2),
        ]
        out = pd.concat([df, pd.DataFrame([jami_row], columns=EXCEL_COLUMNS)],
                        ignore_index=True)
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            out.to_excel(writer, index=False, sheet_name="Hisob-kitob")
        return output_path


# --------------------------------------------------------------------------
# Oddiy funksiyalar (modul darajasi)
# --------------------------------------------------------------------------

def alpha_hisobla(lf: float, lr: float) -> float:
    """Lf va Lr bo'yicha alpha ushlab qolinadigan ulush."""
    return _lookup(_index_100(lf, lr), ALPHA_TABLE)


def beta_hisobla(kamal: int, kstjb: int) -> float:
    """Kamal va Kstjb bo'yicha beta ushlab qolinadigan ulush."""
    return _lookup(_index_100(kamal - kstjb, kamal), BETA_TABLE)


def gamma_hisobla(kamal: int, kmaq: int) -> float:
    """Kamal va Kmaq bo'yicha gamma ushlab qolinadigan ulush."""
    return _lookup(_index_100(kamal - kmaq, kamal), GAMMA_TABLE)


def tolov_hisobla(skm: float, lf: float, lr: float,
                  kamal: int, kstjb: int, kmaq: int) -> dict:
    """SKM va ko'rsatkichlar asosida to'lovni hisoblaydi.

    Qaytaradi: {"alpha", "beta", "gamma", "tolov"} — formulaga muvofiq:
    S = SKM * Lf * (1 - alpha) * (1 - beta) * (1 - gamma).
    """
    if not lr or not kamal:
        alpha, beta, gamma = MAX_ALPHA, MAX_BETA, MAX_GAMMA
    else:
        alpha = alpha_hisobla(lf, lr)
        beta = beta_hisobla(kamal, kstjb)
        gamma = gamma_hisobla(kamal, kmaq)
    tolov = skm * float(lf) * (1.0 - alpha) * (1.0 - beta) * (1.0 - gamma)
    return {
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "tolov": max(tolov, 0.0),
    }


def load_excel(path: str, sheet: str | int = 0) -> pd.DataFrame:
    """Excel fayldan hisobot DataFramesi'ni yuklaydi.

    Args:
        path: xlsx fayl yo'li.
        sheet: varaq nomi yoki indeksi.

    Returns:
        DataFrame (kolonkalar SOURCE_COLUMNS bo'yicha normalizatsiya qilinadi).
    """
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    # ustun nomlarini normalizatsiya qilish (format bo'yicha)
    rename: dict[str, str] = {
        "Sana": "sana", "Grafik": "grafik", "Davlat raqami": "davlat_raqami",
        "F.I.O": "fio", "Lr (km)": "lr", "Lf (km)": "lf", "Kamal": "kamal",
        "Kstjb": "kstjb", "Kmaq": "kmaq",
    }
    df = df.rename(columns=rename)
    for col in SOURCE_COLUMNS:
        if col not in df.columns:
            df[col] = 0
    return df[SOURCE_COLUMNS]


def payment_due_date(report_month: date) -> date:
    """To'lov sanasini hisoblaydi (33-band).

    To'lov hisobot oyidan keyingi oyning 15-sanasiga qadar amalga oshiriladi.

    Args:
        report_month: hisobot oyining istalgan sanasi (masalan, 2024-06-10).

    Returns:
        Keyingi oyning 15-sanasi (masalan, 2024-07-15).
    """
    year, month = report_month.year, report_month.month
    if month == 12:
        year, month = year + 1, 1
    else:
        month += 1
    return date(year, month, 15)