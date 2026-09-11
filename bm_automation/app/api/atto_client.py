"""ATTO Dashboard API client (adminpanel.atto.uz)."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

_LOG = logging.getLogger(__name__)

_BASE = "https://adminpanel.atto.uz/v1.0/operator"
_TOKEN_FILE = Path(__file__).resolve().parents[2] / "atto_tokens.json"

# Kredensiallar FAQAT .env'dan o'qiladi (ATTO_USERNAME/ATTO_PASSWORD) —
# hardcode qilish taqiqlangan (2026-09-12 audit). Yo'q bo'lsa klient init'da xato beradi.
_USERNAME = os.getenv("ATTO_USERNAME", "")
_PASSWORD = os.getenv("ATTO_PASSWORD", "")

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class ATTOClient:
    """ATTO dashboard API client. Token is a UUID sent as headers.token."""

    def __init__(self, username: str | None = None, password: str | None = None):
        self.username = username or _USERNAME
        self.password = password or _PASSWORD
        if not self.username or not self.password:
            raise ValueError(
                "ATTO_USERNAME/ATTO_PASSWORD .env faylida ko'rsatilmagan.")
        self.token: str | None = None
        self.client_token: str = str(uuid.uuid4())
        self._http = httpx.Client(
            base_url=_BASE,
            timeout=60,
            follow_redirects=True,
            verify=False,
        )

    # ── Token persistence ───────────────────────────────────────────────
    def _save_token(self) -> None:
        if self.token:
            _TOKEN_FILE.write_text(
                json.dumps({"token": self.token, "username": self.username}, indent=2),
                encoding="utf-8",
            )

    def _load_token(self) -> bool:
        if _TOKEN_FILE.exists():
            try:
                data = json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
                self.token = data.get("token")
                return bool(self.token)
            except Exception:
                pass
        return False

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"language": "uz"}
        if self.token:
            h["token"] = self.token
        return h

    # ── Auth ────────────────────────────────────────────────────────────
    def _get_captcha(self) -> tuple[str, str]:
        """GET /login/get-captcha -> returns (client_token, svg_html)."""
        self.client_token = str(uuid.uuid4())
        r = self._http.get(
            "/login/get-captcha",
            params={"token": self.client_token},
            headers={"language": "uz", "token": self.client_token},
        )
        r.raise_for_status()
        return self.client_token, r.text

    def _login_with_captcha(self, code: str) -> dict:
        """POST /captcha/login with credentials + captcha."""
        payload = {
            "login": self.username,
            "password": self.password,
            "code": code,
            "token": self.client_token,
        }
        r = self._http.post(
            "/captcha/login",
            json=payload,
            headers={**self._headers(), "Content-Type": "application/json"},
        )
        r.raise_for_status()
        resp = r.json()
        access_token = (resp.get("data") or {}).get("accessToken")
        if access_token and _UUID_RE.match(access_token):
            self.token = access_token
            self._save_token()
            _LOG.info("ATTO: login successful, token saved")
        return resp

    def login(self) -> bool:
        """Login: try saved token -> Playwright browser login."""
        if self._load_token() and self._check_token():
            _LOG.info("ATTO: saved token still valid")
            return True

        _LOG.info("ATTO: token invalid, trying Playwright browser login...")
        return self._login_playwright()

    def _login_playwright(self) -> bool:
        """Use Playwright to open browser, user solves captcha, capture token from response."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _LOG.error("ATTO: playwright not installed (pip install playwright)")
            return False

        found_token: dict[str, str | None] = {"token": None}

        def on_response(response):
            if found_token["token"]:
                return
            if "captcha/login" in response.url:
                try:
                    body = response.json()
                    access_token = (body.get("data") or {}).get("accessToken")
                    if access_token and _UUID_RE.match(access_token):
                        found_token["token"] = access_token
                        _LOG.info("ATTO: captured token from login response: %s", access_token[:40])
                except Exception:
                    pass

        def on_request(request):
            if found_token["token"]:
                return
            t = request.headers.get("token", "")
            m = _UUID_RE.search(t)
            if m:
                found_token["token"] = m.group(0)
                _LOG.info("ATTO: captured token from request header: %s", m.group(0)[:40])

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                ctx = browser.new_context(ignore_https_errors=True, locale="uz")
                page = ctx.new_page()
                page.on("request", on_request)
                page.on("response", on_response)

                page.goto("https://dashboard.atto.uz/login", wait_until="domcontentloaded", timeout=120000)
                page.wait_for_load_state("networkidle", timeout=30000)

                page.fill('input[name="login"]', self.username)
                page.fill('input[name="password"]', self.password)

                _LOG.info("ATTO: browser opened — solve captcha and click Login (10min)")
                for _ in range(200):
                    import time
                    time.sleep(3)
                    if found_token["token"]:
                        break

                if found_token["token"]:
                    self.token = found_token["token"]
                    self._save_token()
                    _LOG.info("ATTO: login successful, token saved")
                    browser.close()
                    return True

                _LOG.error("ATTO: token not captured from browser")
                browser.close()
        except Exception as exc:
            _LOG.error("ATTO: Playwright login failed: %s", exc)
        return False

    def _check_token(self) -> bool:
        """Verify token is still valid."""
        try:
            r = self._http.get("/profile", headers=self._headers())
            data = r.json()
            if r.status_code == 200 and data.get("success"):
                return True
            self.token = None
            return False
        except Exception:
            return False

    # ── API helpers ─────────────────────────────────────────────────────
    def _get(self, path: str, params: dict | None = None) -> Any:
        r = self._http.get(path, params=params, headers=self._headers())
        if r.status_code == 401:
            self.token = None
            raise PermissionError("ATTO token expired")
        if r.status_code in (400, 405):
            try:
                body = r.json()
            except Exception:
                body = {"error": r.text}
            _LOG.warning("ATTO: %d for %s — %s", r.status_code, path, json.dumps(body, ensure_ascii=False)[:200])
            return {"data": None, "success": False, "error": body}
        r.raise_for_status()
        return r.json()

    # ── Public API ──────────────────────────────────────────────────────
    def profile(self) -> dict:
        """Get current user profile."""
        return self._get("/profile")

    def merchants(self, transport_type: str = "bus") -> list[dict]:
        """List merchants (parks/companies)."""
        data = self._get("/dashboard/transaction/merchants", {"type": transport_type})
        return data.get("data", {}).get("merchants", [])

    def routes(self, merchant_id: str | None = None) -> list[dict]:
        """List routes. Requires merchant_id."""
        if not merchant_id:
            merchants = self.merchants()
            if merchants:
                merchant_id = merchants[0].get("id", "")
        if not merchant_id:
            return []
        data = self._get("/dashboard/transaction/routes", {"merchantId": merchant_id})
        return data.get("data", {}).get("routes", [])

    def transports(self, merchant_id: str | None = None, route_id: str | None = None) -> list[dict]:
        """List transports (buses)."""
        if not merchant_id:
            merchants = self.merchants()
            if merchants:
                merchant_id = merchants[0].get("id", "")
        if not merchant_id:
            return []
        params: dict[str, Any] = {"type": "bus", "merchantId": merchant_id}
        if route_id:
            params["routeId"] = route_id
        data = self._get("/dashboard/transaction/transports", params)
        return data.get("data", {}).get("transports", [])

    def _merchant_login(self, merchant_id: str | None = None) -> str:
        """Get merchant login string for API calls."""
        if not merchant_id:
            merchants = self.merchants()
            if merchants:
                merchant_id = merchants[0].get("id", "")
        if not merchant_id:
            return ""
        merchants = self.merchants()
        for m in merchants:
            if m.get("id") == merchant_id:
                return m.get("login", "")
        return ""

    def _first_route_id(self, merchant_id: str | None = None) -> str:
        """Get first route ID for merchant."""
        routes = self.routes(merchant_id)
        if routes:
            return routes[0].get("id", "")
        return ""

    def _iso(self, d: date | str | None, end: bool = False) -> str:
        """Format date as ISO string for API (always includes time)."""
        if d is None:
            return self._iso(date.today(), end=end)
        if isinstance(d, str):
            if "T" in d:
                return d
            suffix = "T23:59:59" if end else "T00:00:00"
            return d.split(" ")[0] + suffix
        suffix = "T23:59:59" if end else "T00:00:00"
        return d.strftime("%Y-%m-%d") + suffix

    def bus_report(
        self,
        from_date: str | date | None = None,
        to_date: str | date | None = None,
        merchant_id: str | None = None,
        route_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        """Fetch bus transaction report with correct SPA params."""
        if from_date is None:
            from_date = date.today() - timedelta(days=5)
        if to_date is None:
            to_date = date.today()

        if not merchant_id:
            merchants = self.merchants()
            if merchants:
                merchant_id = merchants[0].get("id", "")

        m_login = self._merchant_login(merchant_id)
        r_id = route_id or self._first_route_id(merchant_id)
        r_routes = self.routes(merchant_id)
        r_short = r_routes[0].get("shortName", "") if r_routes else ""

        params: dict[str, Any] = {
            "offset": offset,
            "limit": limit,
            "tripType": "all",
            "statuses": "",
            "dateType": "terminal",
            "transportType": "bus",
            "from": self._iso(from_date),
            "to": self._iso(to_date, end=True),
        }
        if merchant_id:
            params["merchantId"] = merchant_id
        if m_login:
            params["merchantLogins[]"] = m_login
        if r_short:
            params["serviceShortNames[]"] = r_short
        if r_id:
            params["serviceShortId"] = r_id

        data = self._get("/dashboard/transaction/list", params)
        return data if isinstance(data, dict) else {"data": data}

    def z_report(
        self,
        d: date | str | None = None,
        merchant_id: str | None = None,
        dep_id: str | None = None,
    ) -> dict:
        """Fetch Z-report for a specific transport on a date."""
        if d is None:
            d = date.today()
        date_str = d.strftime("%Y-%m-%d") if isinstance(d, date) else d

        if not merchant_id:
            merchants = self.merchants()
            if merchants:
                merchant_id = merchants[0].get("id", "")

        m_login = self._merchant_login(merchant_id)

        params: dict[str, Any] = {
            "date": date_str,
            "transportType": "bus",
            "merchantId": merchant_id,
            "merchant": m_login,
        }
        if dep_id:
            params["depId"] = dep_id

        data = self._get("/dashboard/transaction/z/list", params)
        return data if isinstance(data, dict) else {"data": data}

    def all_counts(
        self,
        from_date: str | date | None = None,
        to_date: str | date | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """Fetch all bus counts summary."""
        if from_date is None:
            from_date = date.today()
        if to_date is None:
            to_date = date.today()

        params = {
            "offset": offset,
            "limit": limit,
            "from": self._iso(from_date),
            "to": self._iso(to_date, end=True),
        }
        data = self._get("/dashboard/transaction/bus/all-counts", params)
        return data if isinstance(data, dict) else {"data": data}

    def fetch_all_bus_report(
        self,
        from_date: str | date | None = None,
        to_date: str | date | None = None,
        merchant_id: str | None = None,
        route_id: str | None = None,
        max_pages: int = 50,
    ) -> list[dict]:
        """Fetch all pages of bus report."""
        all_rows: list[dict] = []
        offset = 0
        limit = 100
        for _ in range(max_pages):
            result = self.bus_report(
                from_date=from_date,
                to_date=to_date,
                merchant_id=merchant_id,
                route_id=route_id,
                offset=offset,
                limit=limit,
            )
            data = result.get("data", {})
            rows = data.get("content", []) if isinstance(data, dict) else []
            all_rows.extend(rows)
            total = data.get("totalElements", 0) if isinstance(data, dict) else 0
            offset += limit
            if offset >= total or not rows:
                break
        _LOG.info("ATTO bus report: fetched %d rows", len(all_rows))
        return all_rows


def login_interactive() -> ATTOClient | None:
    """Login via Playwright browser, return authenticated client."""
    client = ATTOClient()
    if client.login():
        return client
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    c = login_interactive()
    if c:
        print("Login OK, token:", c.token[:40] + "...")
        merchants = c.merchants()
        print(f"Merchants: {len(merchants)} found")
        for m in merchants:
            print(f"  - {m.get('login', '?')}: {m.get('name', '?')}")
        routes = c.routes()
        print(f"Routes: {len(routes)} found")
        for rt in routes:
            print(f"  - {rt.get('shortName', '?')}: {rt.get('name', '?')}")
        counts = c.all_counts()
        items = counts.get("data", {}).get("items", [])
        print(f"All counts: {len(items)} merchants")
        for item in items:
            for route in item.get("routes", []):
                for bus in route.get("buses", []):
                    print(f"  {bus.get('name')}: cards={bus.get('cards',0)} qr={bus.get('qr',0)}")
