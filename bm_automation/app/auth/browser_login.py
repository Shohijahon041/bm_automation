"""OneID orqali brauzer avtorizatsiyasi.

Saytga Playwright (Chromium) bilan kiradi:
1. /sign-in sahifasida OneID tugmasini bosadi
2. OneID (id.egov.uz) login/parolni to'ldiradi
3. (ixtiyoriy) SMS kodni so'raydi / kiritadi
4. Kompaniyani tanlaydi
5. access_token / refresh_token ni tokens.json ga saqlaydi

Foydalanish:
    python -m bm_automation login-browser            # .env dagi login/parol bilan
    python -m bm_automation login-browser --visible  # brauzer oynasi ko'rinadi
    python -m bm_automation login-browser --interactive  # OneID ni o'zingiz to'ldirasiz
"""

from __future__ import annotations

import sys

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from ..config.settings import get_config
from ..core.tokens import save_tokens

SITE_BASES = {
    "https://testapi-bm.dtransport.uz": "https://test-bm.dtransport.uz",
    "https://bmapi.dtransport.uz": "https://bm.dtransport.uz",
}

ONEID_HREF_PART = "sso.egov.uz"


def _site_base(api_base: str) -> str:
    return SITE_BASES.get(api_base, "https://bm.dtransport.uz")


def _capture_profiles(page):
    """login-via-oneid javobidagi profillar ro'yxatini ushlaydi."""
    captured = []

    def on_response(response):
        url = response.url
        if "login-via-oneid/profiles" in url and response.status == 200:
            try:
                body = response.json()
                data = body.get("data") or body
                if isinstance(data, dict):
                    profiles = data.get("profiles") or []
                    captured.extend(profiles)
            except Exception:
                pass

    page.on("response", on_response)
    return captured


def _fill_oneid(page, username: str, password: str) -> bool:
    try:
        page.wait_for_selector('input[name="login"]', timeout=30000)
        page.fill('input[name="login"]', username)
        page.fill('input[name="password"]', password)
        page.click('form button[type="submit"], button[type="submit"]')
        return True
    except PWTimeout:
        return False


def _handle_sms_if_needed(page, interactive: bool) -> None:
    try:
        page.wait_for_selector('input[name="code"], input[name="smsCode"], input[placeholder*="kod" i], input[placeholder*="SMS" i]', timeout=8000)
    except PWTimeout:
        return
    print("SMS kod talab qilinmoqda.")
    if interactive:
        code = input("SMS kodni kiriting: ").strip()
        sms_input = page.query_selector('input[name="code"], input[name="smsCode"], input[placeholder*="kod" i], input[placeholder*="SMS" i]')
        if sms_input:
            sms_input.fill(code)
            page.keyboard.press("Enter")
            page.wait_for_timeout(3000)
    else:
        print("Iltimos brauzer oynasida SMS kodni o'zingiz kiriting.")
        try:
            page.wait_for_url("**/login/oneid**", timeout=180000)
        except PWTimeout:
            pass


def _select_organization(page, want: str):
    """Kompaniya tanlash sahifasida kerakli tashkilotni tanlaydi."""
    try:
        page.wait_for_url("**/sign-in/profiles**", timeout=60000)
    except PWTimeout:
        return False

    cards = page.query_selector_all("div.rounded-lg.overflow-hidden.cursor-pointer")
    if not cards:
        cards = page.query_selector_all("div[class*=cursor-pointer]")

    names = []
    for card in cards:
        text = card.inner_text() if card else ""
        names.append(text.replace("\n", " | "))
        if want and (want.lower() in text.lower()):
            card.click()
            page.wait_for_timeout(500)
            break
    else:
        if not cards:
            print("Hech qanday kompaniya karta topilmadi")
            return False
        if not want:
            cards[0].click()
            page.wait_for_timeout(500)
        else:
            print(f"'{want}' topilmadi. Mavjudlar:")
            for n in names:
                print("   ", n)
            return False

    page.query_selector('button[type="primary"], button:has-text("Davom etish"), button:has-text("Продолжить")').click()
    return True


def browser_login(
    username: str = "",
    password: str = "",
    organization: str = "",
    interactive: bool = False,
    headless: bool = False,
    timeout: int = 240,
) -> dict:
    """Brauzer orqali OneID login bajarib tokenlarni qaytaradi."""
    cfg = get_config()
    username = username or cfg.username
    password = password or cfg.password
    organization = organization or cfg.organization
    site = _site_base(cfg.base_url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(ignore_https_errors=True, locale="uz")
        page = context.new_page()
        captured = _capture_profiles(page)

        page.goto(f"{site}/sign-in", wait_until="load", timeout=60000)
        page.wait_for_timeout(2000)
        link = None
        selectors = [
            f'a[href*="{ONEID_HREF_PART}"]',
            'a[href*="egov.uz"]',
            'a[href*="oneid"]',
            'a:has-text("OneID")',
            'button:has-text("OneID")',
        ]
        for attempt in range(1, 6):
            for sel in selectors:
                try:
                    link = page.query_selector(sel)
                except Exception:
                    link = None
                if link:
                    break
            if link:
                break
            # Diagnostika: sahifa holatini yozib qo'yamiz
            try:
                print(f"  OneID tugmasi topilmadi ({attempt}) | URL: {page.url}")
                body = page.inner_text("body")[:400]
                if body.strip():
                    print("  Sahifa matni:", body.replace("\n", " | ")[:400])
            except Exception:
                pass
            try:
                page.goto(f"{site}/sign-in", wait_until="load", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(5000)
        if not link:
            raise RuntimeError("OneID tugmasi topilmadi")
        page.goto(link.get_attribute("href"), wait_until="load", timeout=45000)

        print(f"OneID sahifasi: {page.url}")
        if username and password:
            _fill_oneid(page, username, password)
        else:
            interactive = True

        if interactive:
            print("Iltimos, OneID'ga o'zingiz kiring...")
        page.wait_for_url("**/login/oneid**", timeout=timeout * 1000)

        _handle_sms_if_needed(page, interactive)

        if not _select_organization(page, organization):
            try:
                page.wait_for_url("**/", timeout=20000)
            except PWTimeout:
                pass

        page.wait_for_timeout(3000)
        tokens = page.evaluate(
            """() => ({
                access_token: localStorage.getItem('access_token') || localStorage.getItem('token'),
                refresh_token: localStorage.getItem('refresh_token'),
                org: localStorage.getItem('organization') || ''
            })"""
        )
        browser.close()

        if not tokens.get("access_token"):
            raise RuntimeError("Token olinmadi. OneID login yoki kompaniya tanlashda xato bo'ldi.")

        save_tokens(
            tokens["access_token"],
            tokens["refresh_token"],
            base_url=cfg.base_url,
            organization=tokens.get("org") or organization,
        )
        print(f"Token saqlandi. Kompaniya: {tokens.get('org') or organization or 'noma\'lum'}")
        print(f"  Profillar (agar ko'p bo'lsa): {len(captured)}")
        for prof in captured[:10]:
            print("   -", prof.get("organizationName") or prof.get("fullName"), "/", prof.get("role") or "", "/", prof.get("tin") or "")
        if captured:
            from ..core.profiles import discover_from_oneid

            added = discover_from_oneid(captured)
            if added:
                print(f"  profiles.json'ga saqlandi: {len(added)} profil")
        return tokens


def run(argv: list | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="bm_automation login-browser")
    parser.add_argument("--visible", action="store_true", help="Brauzer oynasini ko'rsatish")
    parser.add_argument("--interactive", action="store_true", help="OneID ma'lumotlarini o'zingiz kiritish")
    parser.add_argument("--user", default="", help="OneID login")
    parser.add_argument("--password", default="", help="OneID parol")
    parser.add_argument("--org", default="", help="Tanlanadigan kompaniya nomi (ixtiyoriy)")
    args = parser.parse_args(argv)

    try:
        tokens = browser_login(
            username=args.user,
            password=args.password,
            organization=args.org,
            interactive=args.interactive,
            headless=not args.visible,
        )
        print("Bajarildi.")
        return 0
    except Exception as exc:
        print(f"XATO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())
