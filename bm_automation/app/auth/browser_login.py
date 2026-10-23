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

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

# Brauzer ishga tushish kutish vaqti. Sukut bo'yicha Playwright 180s kutadi;
# xotira tangis serverlarda bu uzoq thrash'ga olib keladi — qisqartiramiz.
LAUNCH_TIMEOUT_MS = int(os.getenv("BM_BROWSER_LAUNCH_TIMEOUT_MS", "60000"))

# Muvaffaqiyatsiz brauzer login'dan keyin qayta urinishni bloklash oynasi
# (sekund). Server IP'da brauzer login umuman ishlamaydi — takror urinishlar
# har safar Chromium ochib xotirani yeydi. Shu sababli cooldown qo'llanadi.
COOLDOWN_S = float(os.getenv("BM_BROWSER_LOGIN_COOLDOWN_S", "600"))


def _cooldown_path() -> Path:
    default = Path("state") / "browser_login_cooldown.json"
    return Path(os.getenv("BM_BROWSER_LOGIN_STATE", str(default)))


def _cooldown_remaining() -> float:
    """Cooldown qolgan sekundlar (0 bo'lsa bloklanmagan)."""
    try:
        data = json.loads(_cooldown_path().read_text(encoding="utf-8"))
        last = float(data.get("failed_at", 0.0))
    except Exception:  # noqa: BLE001 - fayl yo'q/buzilgan bo'lsa blok yo'q
        return 0.0
    return max(0.0, COOLDOWN_S - (time.time() - last))


def _mark_failed() -> None:
    try:
        path = _cooldown_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"failed_at": time.time()}), encoding="utf-8")
    except Exception:  # noqa: BLE001 - holat yozilmasa ham davom etamiz
        pass


def _clear_failed() -> None:
    try:
        _cooldown_path().unlink()
    except Exception:  # noqa: BLE001
        pass

from ..config.settings import get_config
from ..core.tokens import save_tokens

SITE_BASES = {
    "https://testapi-bm.dtransport.uz": "https://test-bm.dtransport.uz",
    "https://bmapi.dtransport.uz": "https://bm.dtransport.uz",
}

ONEID_HREF_PART = "sso.egov.uz"

# /sign-in sahifasi ba'zi IP'larda (masalan, server — datacenter IP) 403
# qaytaradi. Shu sababli to'g'ridan-to'g'ri SSO oqimi ishlatiladi:
# id.egov.uz'da login qilinadi, qaytish havolasidagi `code` ushlanadi
# (bloklangan sahifa yuklanmaydi) va BM API orqali token olinadi.
PROD_SITE = "https://bm.dtransport.uz"
DIRECT_SSO_URL = (
    "https://sso.egov.uz/sso/oauth/Authorization.do?response_type=one_code"
    "&client_id=bm_dtransport_uz"
    "&redirect_uri=https%3A%2F%2Fbm.dtransport.uz%2Flogin%2Foneid"
    "&scope=myportal&state=testState"
)


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


# OneID login/paroldan keyin "Qanday rolda davom etmoqchisiz?" dialogi
# chiqadi (Jismoniy shaxs / Yuridik shaxs ... + "Davom etish" tugmasi). Rol
# tanlanmasa oqim shu yerda to'xtaydi va `code` olinmaydi. Tanlanadigan rol
# matni `BM_ONEID_ROLE` bilan beriladi (default "Jismoniy shaxs"); topilmasa
# birinchi karta bosiladi.
ROLE_DIALOG_TIMEOUT_MS = int(os.getenv("BM_ONEID_ROLE_TIMEOUT_MS", "15000"))


def _role_want() -> str:
    return os.getenv("BM_ONEID_ROLE", "Jismoniy shaxs").strip()


def _click_role_text(page, want: str) -> bool:
    """Rol kartasini matn bo'yicha (topilmasa birinchisini) bosadi."""
    if want:
        try:
            loc = page.get_by_text(want, exact=False)
            if loc.count():
                loc.first.click(timeout=5000)
                return True
        except Exception:  # noqa: BLE001 - matn topilmasa birinchi kartaga tushamiz
            pass
    for sel in ('.MuiDialog-root [role="dialog"] .MuiDialogContent-root p',
                '.MuiDialog-root [role="dialog"] h6'):
        try:
            el = page.query_selector(sel)
            if el:
                el.click(timeout=5000)
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _handle_role_dialog(page, want: str) -> bool:
    """OneID rol tanlash dialogini bosib o'tadi (bo'lmasa darhol qaytadi).

    Dialog paydo bo'lsa: matn bo'yicha rol kartasi (default "Jismoniy shaxs")
    bosiladi, so'ng "Davom etish" tugmasi bosiladi. Qaytaradi: dialog
    topildimi.
    """
    try:
        page.wait_for_selector('.MuiDialog-root [role="dialog"]',
                               timeout=ROLE_DIALOG_TIMEOUT_MS)
    except PWTimeout:
        return False
    if not _click_role_text(page, want):
        return False
    page.wait_for_timeout(700)
    btn = page.query_selector('.MuiDialog-root [role="dialog"] button[type="submit"]')
    if btn is not None and not btn.is_disabled():
        try:
            btn.click(timeout=5000)
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            page.click('.MuiDialog-root [role="dialog"] button:has-text("Davom etish")',
                       timeout=5000)
        except Exception:  # noqa: BLE001
            pass
    try:
        page.wait_for_selector('.MuiDialog-root [role="dialog"]', state="hidden",
                               timeout=10000)
    except PWTimeout:
        pass
    return True


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


def _direct_sso_code(username: str, password: str, headless: bool, timeout: int) -> str:
    """id.egov.uz orqali kirib, qaytish havolasidagi OneID `code`ni ushlaydi.

    Bloklangan /sign-in sahifasi umuman yuklanmaydi — faqat sso.egov.uz /
    id.egov.uz ishlatiladi; qaytish URL'i (`/login/oneid?code=...`) esa
    yuklanmasdan bekor qilinadi va `code` ushlanadi.
    """
    code_box = {"code": ""}

    def on_request(request):
        url = request.url
        if "/login/oneid" in url and "code=" in url and not code_box["code"]:
            code_box["code"] = url.split("code=")[1].split("&")[0]
            try:
                request.abort()
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, timeout=LAUNCH_TIMEOUT_MS)
        context = browser.new_context(ignore_https_errors=True, locale="uz")
        page = context.new_page()
        page.on("request", on_request)
        page.goto(DIRECT_SSO_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        if not _fill_oneid(page, username, password):
            browser.close()
            raise RuntimeError("OneID login formasi topilmadi")
        _handle_sms_if_needed(page, interactive=not headless)
        if not headless:
            # Ko'rinadigan rejimda rol tanlashni foydalanuvchi bajaradi.
            pass
        else:
            _handle_role_dialog(page, _role_want())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not code_box["code"]:
            page.wait_for_timeout(500)
        browser.close()

    if not code_box["code"]:
        raise RuntimeError("OneID code olinmadi (login yoki 2FA bajarilmadi)")
    return code_box["code"]


def _exchange_code(code: str, organization: str, discover: bool = True) -> dict:
    """OneID `code`ni tokenlarga almashtiradi va tokens.json'ga saqlaydi.

    `discover=False` — aniqlangan profillarni `profiles.json`ga yozmaslik
    (faqat natija qaytariladi, onboarding keyinroq qo'shadi).
    """
    from ..api.client import BMClient

    client = BMClient()
    data = client.login_via_oneid(code)
    token = (data or {}).get("token") or {}
    profiles = (data or {}).get("profiles") or []
    access = token.get("access_token")
    refresh = token.get("refresh_token")
    if not access:
        raise RuntimeError("OneID login access token bermadi")
    org = organization
    if not org and profiles:
        org = profiles[0].get("organizationName") or profiles[0].get("fullName") or ""
    save_tokens(access, refresh, base_url=client.config.base_url, organization=org)
    label = org or "noma'lum"
    print(f"Token saqlandi (to'g'ridan-to'g'ri SSO). Kompaniya: {label}")
    print(f"  Profillar: {len(profiles)}")
    for prof in profiles[:10]:
        print("   -", prof.get("organizationName") or prof.get("fullName"), "/",
              prof.get("role") or "", "/", prof.get("tin") or "")
    if profiles and discover:
        from ..core.profiles import discover_from_oneid

        added = discover_from_oneid(profiles)
        if added:
            print(f"  profiles.json'ga saqlandi: {len(added)} profil")
    return {"access_token": access, "refresh_token": refresh, "org": org,
            "profiles": list(profiles)}


def browser_login(
    username: str = "",
    password: str = "",
    organization: str = "",
    interactive: bool = False,
    headless: bool = False,
    timeout: int = 240,
    force: bool = False,
    discover: bool = True,
) -> dict:
    """Brauzer orqali OneID login bajarib tokenlarni qaytaradi.

    Oldingi urinish muvaffaqiyatsiz bo'lsa, cooldown davomida brauzer
    ochilmaydi (`force=True` bilan chetlab o'tiladi).
    """
    if not force:
        remaining = _cooldown_remaining()
        if remaining > 0:
            raise RuntimeError(
                f"brauzer login vaqtincha bloklangan ({int(remaining)}s qoldi)"
            )
    try:
        result = _browser_login_impl(
            username, password, organization, interactive, headless, timeout,
            discover=discover,
        )
    except Exception:
        _mark_failed()
        raise
    _clear_failed()
    return result


def _browser_login_impl(
    username: str = "",
    password: str = "",
    organization: str = "",
    interactive: bool = False,
    headless: bool = False,
    timeout: int = 240,
    discover: bool = True,
) -> dict:
    """Brauzer orqali OneID login bajarib tokenlarni qaytaradi."""
    cfg = get_config()
    username = username or cfg.username
    password = password or cfg.password
    organization = organization or cfg.organization
    site = _site_base(cfg.base_url)

    # Avval to'g'ridan-to'g'ri SSO oqimi (server kabi /sign-in 403 bo'lgan
    # muhitlarda ham ishlaydi). Muvaffaqiyatsiz bo'lsa /sign-in oqimi.
    if site == PROD_SITE and username and password:
        try:
            code = _direct_sso_code(username, password, headless, timeout)
            return _exchange_code(code, organization, discover=discover)
        except Exception as exc:  # noqa: BLE001 - fallback ataylab
            print(f"  to'g'ridan-to'g'ri SSO ishlamadi ({exc}); /sign-in oqimiga o'tilmoqda")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, timeout=LAUNCH_TIMEOUT_MS)
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
                body = page.inner_text("body")[:200]
                if body.strip():
                    print("  Sahifa matni:", body.replace("\n", " | ")[:200])
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
            _handle_role_dialog(page, _role_want())
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
        if captured and discover:
            from ..core.profiles import discover_from_oneid

            added = discover_from_oneid(captured)
            if added:
                print(f"  profiles.json'ga saqlandi: {len(added)} profil")
        # Natijaga OneID profillari ham qo'shiladi (onboarding: korxona va
        # yo'nalishlarni ro'yxatga olish uchun) — bu yerdan profile yozilmaydi.
        result = dict(tokens)
        result["profiles"] = list(captured)
        return result


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
            force=args.interactive or args.visible,
        )
        print("Bajarildi.")
        return 0
    except Exception as exc:
        print(f"XATO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())
