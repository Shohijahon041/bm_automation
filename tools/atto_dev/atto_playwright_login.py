"""ATTO login — fill login/password, let user solve captcha + click Login."""
import json
from pathlib import Path

from atto_env import atto_credentials

LOGIN, PASSWORD = atto_credentials()

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("pip install playwright && playwright install chromium")
    exit(1)

TOKEN_FILE = Path("C:/Users/User/OneDrive/Документы/Default Project/atto_tokens.json")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(ignore_https_errors=True, locale="uz")
    page = ctx.new_page()

    all_admin = []
    def on_req(req):
        if "adminpanel.atto.uz" in req.url:
            all_admin.append({"method": req.method, "url": req.url, "headers": dict(req.headers)})
    page.on("request", on_req)

    print("[1] Opening ATTO login...")
    page.goto("https://dashboard.atto.uz/login", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=30000)

    print("[2] Filling login & password...")
    page.fill('input[name="login"]', LOGIN)
    page.fill('input[name="password"]', PASSWORD)

    print("\n[3] Browser ochiq! CAPTCHA ni yeching va LOGIN tugmasini bosing.")
    print("    (120 soniya kutaman...)")

    try:
        # Wait for actual navigation to dashboard after user clicks Login
        page.wait_for_url("**/dashboard/report**", timeout=120000)
        print("\n[4] Dashboard ga o'tildi!")
    except:
        try:
            page.wait_for_url("**/dashboard**", timeout=5000)
            print("\n[4] Dashboard ga o'tildi!")
        except:
            print(f"\n[4] Hozirgi URL: {page.url}")
            print("[4] Davom etish uchun navigatsiya qilaman...")

    page.wait_for_timeout(5000)

    # Check all adminpanel requests
    print(f"\n=== Adminpanel so'rovlar ({len(all_admin)}) ===")
    token = None
    for r in all_admin:
        print(f"  {r['method']} {r['url'][:100]}")
        h = r["headers"]
        for k in ("token", "authorization", "access_token", "x-client-id"):
            if k in h:
                print(f"    {k}: {h[k][:80]}")
                if len(h[k]) > 30:
                    token = h[k]

    # Try API call from page
    print("\n=== API test ===")
    result = page.evaluate("""
        async () => {
            try {
                const resp = await fetch('https://adminpanel.atto.uz/v1.0/operator/dashboard/transaction/merchants?type=bus', {
                    credentials: 'include',
                    headers: {'language': 'uz', 'accept': 'application/json'}
                });
                return {status: resp.status, body: (await resp.text()).substring(0, 300)};
            } catch(e) {
                return {error: e.message};
            }
        }
    """)
    print(f"  {json.dumps(result, ensure_ascii=False)[:500]}")

    if token:
        TOKEN_FILE.write_text(json.dumps({"token": token}, indent=2), encoding="utf-8")
        print(f"\n[OK] Token saved: {token[:60]}...")
    else:
        # Try getting token from page cookies via CDP
        cdp = ctx.new_cdp_session(page)
        cookies = cdp.send("Network.getAllCookies")
        for c in cookies.get("cookies", []):
            if "token" in c.get("name", "").lower() or len(c.get("value", "")) > 50:
                print(f"[CDP] Cookie: {c['name']}={c['value'][:60]}...")
                token = c["value"]
        cdp.detach()

        if token:
            TOKEN_FILE.write_text(json.dumps({"token": token}, indent=2), encoding="utf-8")
            print(f"\n[OK] Token saved: {token[:60]}...")
        else:
            print("[FAIL] Token not captured")

    browser.close()
