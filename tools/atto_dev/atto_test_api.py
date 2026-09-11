"""Test ATTO - find what data is available."""
import httpx, json

TOKEN = "8dc594e7-77d6-4051-90f9-56d4ca2dfd89"
BASE = "https://adminpanel.atto.uz/v1.0/operator"
HEADERS = {"token": TOKEN, "language": "uz", "Accept": "application/json"}

endpoints = [
    ("/dashboard/main/bus-cash", {"from": "2026-08-01 00:00", "to": "2026-08-18 23:59"}),
    ("/dashboard/main/tariffs", {"types": ["bus"]}),
    ("/dashboard/main/trans-cards/new/v2", {}),
    ("/dashboard/bus/aggregator/list", {}),
    ("/dashboard/transaction/merchants", {"type": "bus"}),
    ("/dashboard/transaction/cities", {}),
]

for path, params in endpoints:
    print(f"\n=== {path} ===")
    try:
        r = httpx.get(f"{BASE}{path}", params=params, headers=HEADERS, timeout=30, verify=False)
        print(f"  Status: {r.status_code}")
        if r.status_code == 200:
            body = r.json()
            data = body.get("data", body)
            print(f"  Data: {json.dumps(data, ensure_ascii=False)[:500]}")
        else:
            print(f"  Error: {r.text[:200]}")
    except Exception as e:
        print(f"  Exception: {e}")
