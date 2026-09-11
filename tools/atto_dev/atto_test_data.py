import httpx, json
from datetime import date, timedelta

token = "29d73776-5fc0-4575-bb79-e7b10957d064"
headers = {"token": token, "language": "uz"}
base = "https://adminpanel.atto.uz/v1.0/operator"
merchant_id = "1143403234973909212"

fmt = "%Y-%m-%d %H:%M"

# Try with depIds
print("=== Try: depIds ===")
params = {"offset":0,"limit":10,"tripType":"all","statuses":"","dateType":"terminal","transportType":"bus","from":"2026-08-13 00:00","to":"2026-08-18 23:59","merchantId":merchant_id,"depIds":""}
r = httpx.get(base+"/dashboard/transaction/list", params=params, headers=headers, verify=False, timeout=15)
print(r.status_code, json.dumps(r.json(), ensure_ascii=False)[:500])

# Try fetching all merchants' data
print("\n=== All merchants data ===")
r2 = httpx.get(base+"/dashboard/transaction/merchants", params={"type":"bus"}, headers=headers, verify=False, timeout=15)
merchants = r2.json().get("data",{}).get("merchants",[])
for m in merchants:
    print(f"  Merchant: {m.get('login')} - {m.get('name')} - id={m.get('id')} - login={m.get('login')}")

# Try with depIds for different endpoints
print("\n=== zones ===")
r3 = httpx.get(base+"/dashboard/main/zones", headers=headers, verify=False, timeout=15)
print(r3.status_code, json.dumps(r3.json(), ensure_ascii=False)[:500])

print("\n=== names (bus) ===")
r4 = httpx.get(base+"/dashboard/main/names", params={"types[]":"bus","types[]":"metro"}, headers=headers, verify=False, timeout=15)
print(r4.status_code, json.dumps(r4.json(), ensure_ascii=False)[:500])

# Try z-report
print("\n=== z-report ===")
r5 = httpx.get(base+"/dashboard/transaction/z-report", params={"offset":0,"limit":10,"dateType":"terminal","transportType":"bus","from":"2026-08-13 00:00","to":"2026-08-18 23:59","merchantId":merchant_id}, headers=headers, verify=False, timeout=15)
print(r5.status_code, json.dumps(r5.json(), ensure_ascii=False)[:1000])
