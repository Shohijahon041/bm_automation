"""Browser Tool — Playwright browser automation."""

from __future__ import annotations

from typing import Any

from . import BaseTool
from ..config import get_myai_config
from ...utils.logger import get_logger

log = get_logger("myai.tools.browser")


class BrowserTool(BaseTool):
    """Playwright orqali browser automation.

    bm.dtransport.uz bilan ishlash uchun:
    - open_page: sahifani ochish
    - click: element bosish
    - fill: forma to'ldirish
    - read_table: jadvaldan ma'lumot olish
    - screenshot: skrinshot olish
    """

    name = "browser"
    description = "Playwright browser automation for bm.dtransport.uz"

    def __init__(self):
        self._config = get_myai_config()
        self._playwright = None
        self._browser = None
        self._page = None
        self._available = None  # None = unchecked

    async def _ensure_browser(self):
        """Browser ochiq ekanligini tekshirish."""
        if self._page is not None:
            return
        if self._available is False:
            raise RuntimeError("Playwright mavjud emas")
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._config.browser_headless,
            )
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
            )
            self._page = await context.new_page()
            self._available = True
            log.info("Browser ochildi (headless=%s)", self._config.browser_headless)
        except ImportError:
            self._available = False
            raise RuntimeError(
                "Playwright o'rnatilmagan: pip install playwright && "
                "python -m playwright install chromium"
            )
        except Exception as exc:
            self._available = False
            await self._cleanup()
            raise RuntimeError(f"Browser ochishda xatolik: {exc}")

    async def execute(self, action: str = "", **kwargs) -> Any:
        """Browser tool ni bajarish."""
        actions = {
            "open_page": self._open_page,
            "click": self._click,
            "fill": self._fill,
            "press": self._press,
            "wait": self._wait,
            "read_page": self._read_page,
            "read_table": self._read_table,
            "take_screenshot": self._take_screenshot,
            "get_current_url": self._get_current_url,
            "close": self._close,
        }
        handler = actions.get(action)
        if handler is None:
            raise ValueError(f"Noma'lum browser action: {action}")
        return await handler(**kwargs)

    async def _cleanup(self) -> None:
        """Browser va Playwright resurslarini tozalash."""
        try:
            if self._browser:
                await self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        self._page = None
        self._browser = None
        self._playwright = None

    async def _open_page(self, url: str = "", **kwargs) -> dict:
        await self._ensure_browser()
        if self._page is None:
            raise RuntimeError("Browser page mavjud emas — qayta oching")
        await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        log.info("Sahifa ochildi: %s", url)
        return {"url": url, "title": await self._page.title()}

    async def _click(self, selector: str = "", **kwargs) -> dict:
        if self._page is None:
            raise RuntimeError("Browser page mavjud emas")
        await self._page.click(selector, timeout=10000)
        return {"clicked": selector}

    async def _fill(self, selector: str = "", value: str = "", **kwargs) -> dict:
        if self._page is None:
            raise RuntimeError("Browser page mavjud emas")
        await self._page.fill(selector, value, timeout=10000)
        return {"filled": selector}

    async def _press(self, key: str = "", **kwargs) -> dict:
        if self._page is None:
            raise RuntimeError("Browser page mavjud emas")
        await self._page.keyboard.press(key)
        return {"pressed": key}

    async def _wait(self, selector: str = "", timeout: int = 10000, **kwargs) -> dict:
        if self._page is None:
            raise RuntimeError("Browser page mavjud emas")
        await self._page.wait_for_selector(selector, timeout=timeout)
        return {"waited": selector}

    async def _read_page(self, **kwargs) -> dict:
        if self._page is None:
            raise RuntimeError("Browser page mavjud emas")
        content = await self._page.content()
        return {"html": content[:50000], "url": self._page.url}

    async def _read_table(self, selector: str = "table", **kwargs) -> dict:
        """HTML jadvaldan ma'lumot olish."""
        if self._page is None:
            raise RuntimeError("Browser page mavjud emas")
        rows = await self._page.eval_on_selector_all(
            f"{selector} tr",
            """elements => elements.map(row =>
                Array.from(row.cells).map(cell => cell.textContent.trim())
            )""",
        )
        if not rows:
            return {"headers": [], "rows": []}
        headers = rows[0] if rows else []
        data = rows[1:] if len(rows) > 1 else []
        return {"headers": headers, "rows": data}

    async def _take_screenshot(self, path: str = "", **kwargs) -> dict:
        import tempfile, os
        if self._page is None:
            raise RuntimeError("Browser page mavjud emas")
        if not path:
            path = os.path.join(tempfile.gettempdir(), "myai_screenshot.png")
        await self._page.screenshot(path=path, full_page=True)
        return {"path": path}

    async def _get_current_url(self, **kwargs) -> dict:
        if self._page is None:
            raise RuntimeError("Browser page mavjud emas")
        return {"url": self._page.url}

    async def _close(self, **kwargs) -> dict:
        await self._cleanup()
        return {"closed": True}
