"""ATTO — open browser, wait for user to login and navigate to bus report."""
import json, time, sys
from pathlib import Path

from atto_env import atto_credentials

LOGIN, PASSWORD = atto_credentials()

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("pip install playwright && playwright install chromium")
    exit(1)

captured_api = []
captured_data = {}

def on_request(request):
    if "adminpanel.atto.uz" in request.url:
        h = dict(request.headers)
        captured_api.append({
            "method": request.method,
            "url": request.url,
            "token": h.get("token", ""),
            "post_data": request.post_data,
        })

def on_response(response):
    if "adminpanel.atto.uz" in response.url:
        try:
            body = response.json()
            url = response.url
            if "transaction/list" in url:
                captured_data["transaction_list"] = {"url": url, "status": response.status, "body": body}
                print(f"\n[API] transaction/list: {response.status} - {json.dumps(body, ensure_ascii=False)[:500]}")
            elif "merchants" in url:
                captured_data["merchants"] = body
            elif "routes" in url:
                captured_data["routes"] = body
        except:
            pass

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(ignore_https_errors=True, locale="uz")
    page = ctx.new_page()
    page.on("request", on_request)
    page.on("response", on_response)

    print("=" * 60)
    print("ATTO DASHBOARD — Brauzer ochildi!")
    print("=" * 60)
    print()
    print(f"1. Login: {LOGIN}")
    print("2. Parol: (kiritildi, ekranda ko'rsatilmaydi)")
    print("3. CAPTCHA ni yeching va KIRISH bosing")
    print("4. Dashboard ga kirganingizdan keyin:")
    print("   - Hisobotlar > Mashrut bo'limiga boring")
    print("   - Sana tanlang (5 kun)")
    print("   - QIDIRISH bosing")
    print("   - Ma'lumotlar yuklangach, shu terminalda ENTER bosing")
    print()
    print("Siz ishlayotganda men API so'rovlarini kuzataman...")
    print()

    page.goto("https://dashboard.atto.uz/login", wait_until="domcontentloaded", timeout=60000)
    page.fill('input[name="login"]', LOGIN)
    page.fill('input[name="password"]', PASSWORD)

    # Poll for login success (URL changes away from /login)
    print("[KUTISH] Login uchun brauzerda ishlang...")
    start = time.time()
    timeout = 300  # 5 minutes
    while time.time() - start < timeout:
        time.sleep(3)
        url = page.url
        elapsed = int(time.time() - start)
        if "/login" not in url and "dashboard" in url:
            print(f"\n[OK] Login muvaffaqiyatli ({elapsed}s)! URL: {url}")
            break
        if elapsed % 30 == 0 and elapsed > 0:
            print(f"  ... {elapsed}s o'tdi, hali login sahifasida... ({len(captured_api)} ta API so'rov)")
    else:
        print(f"\n[TIMEOUT] {timeout}s kutildi")
        browser.close()
        exit(1)

    # Wait for SPA to fully initialize
    page.wait_for_timeout(5000)
    print(f"[INFO] Dashboard URL: {page.url}")
    print(f"[INFO] API so'rovlar: {len(captured_api)}")

    # Now poll for user to navigate to bus report and search
    print("\n[KUTISH] Endi bus report sahifasiga boring va qidiring...")
    print("         Ma'lumotlar yuklangach ENTER bosing (yoki 180s kutaman)")

    start2 = time.time()
    timeout2 = 180
    found_report = False
    while time.time() - start2 < timeout2:
        time.sleep(3)
        if "transaction_list" in captured_data:
            found_report = True
            print(f"\n[OK] Transaction data topildi!")
            break
        elapsed = int(time.time() - start2)
        if elapsed % 30 == 0 and elapsed > 0:
            print(f"  ... {elapsed}s o'tdi, {len(captured_api)} ta API so'rov, transaction_list hali yo'q...")

    # Final scraping
    print(f"\n{'='*60}")
    print("YAKUNIY NATIJALAR")
    print(f"{'='*60}")

    print(f"\nURL: {page.url}")
    print(f"Jami API so'rovlar: {len(captured_api)}")
    for r in captured_api:
        if r["token"]:
            print(f"  {r['method']} {r['url'][:100]} [token={r['token'][:30]}...]")

    # Scrape table
    tables = page.evaluate("""
        () => {
            const results = [];
            document.querySelectorAll('table').forEach((table) => {
                const headers = [];
                table.querySelectorAll('thead th').forEach(th => headers.push(th.innerText.trim()));
                const rows = [];
                table.querySelectorAll('tbody tr').forEach(tr => {
                    const cells = [];
                    tr.querySelectorAll('td').forEach(td => cells.push(td.innerText.trim()));
                    if (cells.length > 0) rows.push(cells);
                });
                if (rows.length > 0) results.push({headers, rowCount: rows.length, rows});
            });
            return results;
        }
    """)
    print(f"\nJadvallar: {len(tables)}")
    for t in tables:
        print(f"\n  Ustunlar: {t['headers']}")
        print(f"  Qatorlar: {t['rowCount']}")
        for row in t["rows"][:15]:
            print(f"    {row[:10]}")

    # Save everything
    out = {
        "url": page.url,
        "captured_api": captured_api,
        "captured_data": captured_data,
        "tables": tables,
    }
    out_path = Path("C:/Users/User/OneDrive/Документы/Default Project/atto_report_data.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[SAQLANDI] {out_path}")

    browser.close()
