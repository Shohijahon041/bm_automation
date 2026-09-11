"""ATTO — login, use SPA nav to bus report, capture API."""
import json, time
from pathlib import Path

from atto_env import atto_credentials

LOGIN, PASSWORD = atto_credentials()

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("pip install playwright && playwright install chromium")
    exit(1)

captured_api = []
captured_responses = []

def on_request(request):
    if "adminpanel.atto.uz" in request.url:
        captured_api.append({
            "method": request.method,
            "url": request.url,
            "headers": {k: v for k, v in request.headers.items()},
            "post_data": request.post_data,
        })

def on_response(response):
    if "adminpanel.atto.uz" in response.url and "captcha" not in response.url:
        try:
            body = response.json()
            captured_responses.append({
                "url": response.url,
                "status": response.status,
                "body": body,
            })
        except:
            pass

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(ignore_https_errors=True, locale="uz")
    page = ctx.new_page()
    page.on("request", on_request)
    page.on("response", on_response)

    # Step 1: Login
    print("[1] Opening ATTO login...")
    page.goto("https://dashboard.atto.uz/login", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=30000)

    page.fill('input[name="login"]', LOGIN)
    page.fill('input[name="password"]', PASSWORD)
    print("[2] Credentials filled. Solve CAPTCHA and click Login... (120s)")

    try:
        page.wait_for_url("**/dashboard**", timeout=120000)
        print("[3] Login OK! URL:", page.url)
        page.wait_for_timeout(3000)
    except:
        print("[3] Timeout. URL:", page.url)
        browser.close()
        exit(1)

    # Step 2: Find and click bus report link in the SPA navigation
    print("[4] Looking for Report menu...")
    captured_api.clear()
    captured_responses.clear()

    # Try to find the report link by text
    try:
        # Click on "Hisobotlar" or "Report" in the menu
        link = page.locator('a:has-text("Hisobotlar"), a[href*="report"]').first
        if link.count() > 0:
            print(f"[4a] Found report link, clicking...")
            link.click()
            page.wait_for_timeout(3000)
        else:
            # Try sidebar navigation
            print("[4b] No report link found. Checking sidebar...")
            all_links = page.evaluate("""
                () => Array.from(document.querySelectorAll('a')).map(a => ({href: a.href, text: a.innerText.trim().substring(0, 50)}))
            """)
            for l in all_links:
                print(f"  Link: {l['text']} -> {l['href']}")
    except Exception as e:
        print(f"  Error: {e}")

    # Try direct hash navigation (SPA)
    print("[5] Navigating via SPA hash...")
    page.evaluate("window.location.hash = '#/dashboard/report/bus/report'")
    page.wait_for_timeout(5000)

    # Check URL
    print(f"[5a] URL: {page.url}")

    # If still not on report page, try clicking sidebar items
    if "report" not in page.url.lower():
        print("[5b] Trying sidebar click...")
        sidebar_items = page.evaluate("""
            () => Array.from(document.querySelectorAll('[class*="menu"] a, [class*="nav"] a, .ant-menu-item a')).map(el => ({
                text: el.innerText.trim().substring(0, 50),
                href: el.href,
            }))
        """)
        for item in sidebar_items:
            print(f"  Sidebar: {item['text']} -> {item['href']}")
            if "report" in item["href"].lower() or "hisobot" in item["text"].lower():
                page.click(f'a[href="{item["href"]}"]')
                page.wait_for_timeout(3000)
                break

    print(f"\n[6] Current URL: {page.url}")

    # Wait for any API calls
    page.wait_for_timeout(5000)

    # Print all captured API calls
    print(f"\n=== CAPTURED API CALLS ({len(captured_api)}) ===")
    for r in captured_api:
        print(f"\n  {r['method']} {r['url']}")
        for k, v in r["headers"].items():
            if k.lower() in ("token", "content-type", "accept", "language"):
                print(f"    {k}: {v[:80]}")
        if r.get("post_data"):
            print(f"    POST: {r['post_data'][:300]}")

    print(f"\n=== CAPTURED RESPONSES ({len(captured_responses)}) ===")
    for r in captured_responses:
        print(f"\n  {r['status']} {r['url'][:120]}")
        print(f"  Body: {json.dumps(r['body'], ensure_ascii=False)[:500]}")

    # Now try to fill date range and search
    print("\n[7] Looking for date picker and search button...")
    page.wait_for_timeout(3000)
    
    # Print all visible text to understand the page
    body_text = page.evaluate("() => document.body.innerText.substring(0, 2000)")
    print(f"  Page text:\n{body_text[:1500]}")

    # Look for Ant Design date range picker
    date_inputs = page.evaluate("""
        () => {
            const result = [];
            document.querySelectorAll('.ant-picker, input[type="text"], input[placeholder]').forEach(el => {
                result.push({
                    tag: el.tagName,
                    class: el.className.substring(0, 80),
                    placeholder: el.placeholder || '',
                    value: el.value || '',
                    type: el.type || '',
                });
            });
            return result;
        }
    """)
    print(f"\n  Date-like inputs: {json.dumps(date_inputs, ensure_ascii=False)[:1000]}")

    # Save
    out = {
        "url": page.url,
        "api": captured_api,
        "responses": captured_responses,
    }
    Path("C:/Users/User/OneDrive/Документы/Default Project/atto_captured.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print("\n[8] Saved to atto_captured.json")
    print("[8] Browser remains open for 30s — interact with it if needed")
    page.wait_for_timeout(30000)

    browser.close()
