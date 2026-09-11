"""ATTO login — minimal token capture."""
import json, re, time, sys
from pathlib import Path

from atto_env import atto_credentials

LOGIN, PASSWORD = atto_credentials()

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("pip install playwright")
    sys.exit(1)

TOKEN_FILE = Path(__file__).resolve().parent / "atto_tokens.json"
UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
caught = {"token": None}

def on_request(request):
    if caught["token"]:
        return
    t = request.headers.get("token", "")
    m = UUID_RE.search(t)
    if m:
        caught["token"] = m.group(0)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(ignore_https_errors=True, locale="uz")
    page = ctx.new_page()
    page.on("request", on_request)

    page.goto("https://dashboard.atto.uz/login", wait_until="domcontentloaded", timeout=120000)
    page.wait_for_load_state("networkidle", timeout=30000)
    page.fill('input[name="login"]', LOGIN)
    page.fill('input[name="password"]', PASSWORD)

    sys.stdout.write("\n  CAPTCHA YECHING + KIRISH BOSING\n\n")
    sys.stdout.flush()

    for _ in range(200):
        time.sleep(3)
        if caught["token"]:
            break

    if caught["token"]:
        TOKEN_FILE.write_text(json.dumps({"token": caught["token"]}, indent=2), encoding="utf-8")
        sys.stdout.write(f"\n[OK] Token saqlandi: {caught['token']}\n")
        sys.stdout.flush()
    else:
        sys.stdout.write("\nToken topilmadi\n")
        sys.stdout.flush()

    browser.close()
