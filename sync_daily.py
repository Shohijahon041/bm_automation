"""Kunlik avtomatik DB sync: trips + waybills (kecha va bugun, barcha profillar).

Task Scheduler'dagi `BM_DailyReports_2200` (run_daily.cmd) chaqiradi.
Idempotent — istalgancha qayta ishlatish mumkin.
"""
import sys
from datetime import date, timedelta

from bm_automation.app.api.client import BMClient
from bm_automation.app.core.profiles import all_profiles
from bm_automation.app.db import get_storage
from bm_automation.app.db import sync as db_sync


def main() -> int:
    storage = get_storage()
    if not storage.enabled:
        print("DB o'chirilgan (BM_DB_DRIVER/BM_DB_DSN tekshiring).", file=sys.stderr)
        return 1
    client = BMClient()
    client.login()
    today = date.today()
    frm = (today - timedelta(days=1)).isoformat()
    failed = False
    for p in all_profiles():
        name = str(p.get("name") or "?")
        rid = str(p.get("routeVariantId") or "").strip()
        if not rid:
            print(f"  {name}: routeVariantId yo'q", file=sys.stderr)
            failed = True
            continue
        r1 = db_sync.sync_gross_trips(storage, client, rid, today.isoformat())
        print(f"  trips    [{name}] inserted={r1.inserted} updated={r1.updated} "
              f"error={r1.error or '-'}")
        r2 = db_sync.sync_waybills(storage, client, rid, frm, today.isoformat())
        print(f"  waybills [{name}] inserted={r2.inserted} updated={r2.updated} "
              f"deleted={r2.deleted} error={r2.error or '-'}")
        if r1.error or r2.error:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
