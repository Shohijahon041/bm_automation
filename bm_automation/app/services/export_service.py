"""Yo'nalish Jadvali Export — duty/shift ma'lumotini yig'ib Excel'ni to'ldiradi.

Bosqichlar:
  1. Duty (grafik -> haydovchi/avtobus) olinadi, shiftId aniqlanadi.
  2. Shift grafik export xlsx shabloni API'dan yuklanadi.
  3. `app.exporters.export_fill` bloklarga sana/haydovchi/avtobusni yozadi.

Natija `out_dir`ga saqlanadi va fayl yo'li qaytariladi.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..api.client import BMClient
from ..core.profiles import get_profile
from ..exporters.export_fill import fill_export_workbook
from ..repositories.duty_repo import DutyRepository
from ..repositories.shift_repo import ShiftRepository


def build_export(client: BMClient, route_variant_id: str, date_str: str,
                 out_dir=Path("reports"), profile=None,
                 duty_data: dict | None = None,
                 mech_name: str = "",
                 dispatcher_name: str = "") -> Path:
    """Export faylni tayyorlab, yo'lini qaytaradi.

    duty_data berilgan bo'lsa qayta so'rov yubormaydi (tezlik uchun).
    mech_name: mexanik BD nomi
    dispatcher_name: dispetcher nomi
    """
    date_label = datetime.fromisoformat(date_str).strftime("%d.%m.%Y")
    profile = profile if profile is not None else (get_profile() or {})
    route_name = profile.get("routeName") or "yo'nalish"
    safe = re.sub(r'[\\/:*?"<>|]', "_", str(route_name))

    duty = duty_data if duty_data is not None else DutyRepository(client).by_date(route_variant_id, date_str)
    gs = duty.get("graphs") or []
    by_graph = {}
    for g in gs:
        by_graph[(g.get("graphName") or "").upper()] = {
            "driver": g.get("driverName") or "",
            "plate": g.get("plateNum") or "",
            "graph": g.get("graphName") or "",
        }

    shift_repo = ShiftRepository(client)
    sid = duty.get("shiftId")
    if not sid:
        sh = shift_repo.by_date(route_variant_id, date_str)
        sid = (sh or {}).get("id")
    if not sid:
        raise RuntimeError("Shift topilmadi (duty'da shift yo'q)")

    template_bytes = shift_repo.graph_export(sid)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"Yo'nalish_Jadvali_Export_{safe}_{date_str.replace('-', '')}.xlsx"
    return fill_export_workbook(template_bytes, date_label, by_graph, out,
                                graph_name=route_name,
                                mech_name=mech_name,
                                dispatcher_name=dispatcher_name)
