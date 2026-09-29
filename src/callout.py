from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

log = logging.getLogger(__name__)


class CalloutError(RuntimeError):
    """Raised when a Pump.fun callout cannot be published."""


@dataclass(frozen=True)
class CalloutResult:
    status: str
    url: str | None
    message: str


class PumpFunCalloutPublisher:
    """Publish a Pump.fun Callout through the native web UI.

    Pump.fun exposes the Callouts feature in its web application, but no
    documented public create-callout API was found. This adapter therefore
    drives the normal signed-in UI with Playwright. It never handles a seed
    phrase/private key and does not bypass CAPTCHA, bot checks, or other
    access controls.
    """

    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        self.profile_dir = Path(settings.pumpfun_callout_profile_dir)
        self._lock = asyncio.Lock()

    def enabled(self) -> bool:
        return bool(
            self.settings.call_outs_enabled
            and self.settings.pumpfun_callout_mode.upper() == "PLAYWRIGHT"
        )

    def _message(self, *, name: str | None, symbol: str | None, mint: str, amount: float, signature: str) -> str:
        template = self.settings.pumpfun_callout_template
        values = {
            "name": name or "Unknown",
            "symbol": symbol or "TOKEN",
            "mint": mint,
            "amount_sol": f"{amount:.6f}",
            "signature": signature,
            "pump_url": f"https://pump.fun/coin/{mint}",
            "tagline": self.settings.pumpfun_tagline,
            "display_name": self.settings.telegram_trade_name,
        }
        try:
            return template.format(**values)
        except KeyError as exc:
            raise CalloutError(f"Unknown callout template field: {exc}") from exc

    async def publish_after_buy(
        self,
        *,
        mint: str,
        name: str | None,
        symbol: str | None,
        amount_sol: float,
        signature: str,
    ) -> CalloutResult:
        """Publish one callout for a confirmed buy transaction."""
        if not self.settings.call_outs_enabled:
            return CalloutResult("DISABLED", None, "Callouts disabled.")
        if self.settings.pumpfun_callout_mode.upper() != "PLAYWRIGHT":
            return CalloutResult(
                "DISABLED",
                None,
                f"Unsupported callout mode: {self.settings.pumpfun_callout_mode}",
            )
        if not self.settings.pumpfun_callout_accept_terms:
            return CalloutResult(
                "BLOCKED",
                None,
                "Set PUMPFUN_CALLOUT_ACCEPT_TERMS=true after reviewing Pump.fun Callout Terms.",
            )

        text = self._message(
            name=name,
            symbol=symbol,
            mint=mint,
            amount=amount_sol,
            signature=signature,
        )

        async with self._lock:
            self.db.record_callout(
                mint=mint,
                buy_signature=signature,
                status="PENDING",
                message=text,
            )
            try:
                url = await self._publish_ui(mint=mint, message=text)
            except Exception as exc:
                self.db.record_callout_result(mint, signature, "FAILED", None, str(exc))
                self.db.journal(
                    mint,
                    "callout_failed",
                    {"signature": signature, "error": str(exc)},
                )
                raise

            self.db.record_callout_result(mint, signature, "PUBLISHED", url, None)
            self.db.journal(
                mint,
                "callout_published",
                {"signature": signature, "url": url, "message": text},
            )
            return CalloutResult("PUBLISHED", url, text)

    async def _publish_ui(self, *, mint: str, message: str) -> str:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.settings.pumpfun_callout_headless,
                viewport={"width": 1440, "height": 1000},
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    self.settings.pumpfun_callout_create_url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.pumpfun_callout_timeout_ms,
                )
                await self._wait_for_signed_in(page)
                await self._open_callout_composer(page)
                await self._select_coin(page, mint)
                await self._fill_message(page, message)
                await self._post(page)
                return await self._read_callout_url(page, mint)
            finally:
                await context.close()

    async def _wait_for_signed_in(self, page: Page) -> None:
        try:
            await page.get_by_text(re.compile(r"^Sign in$", re.I)).first.wait_for(
                state="visible", timeout=2500
            )
        except PlaywrightTimeoutError:
            return
        raise CalloutError(
            "Pump.fun is not signed in in the saved browser profile. "
            "Run scripts/pumpfun_login.py once with PUMPFUN_CALLOUT_HEADLESS=false."
        )

    async def _open_callout_composer(self, page: Page) -> None:
        # Native Pump.fun flow: Create -> Callout.
        for locator in (
            page.get_by_role("button", name=re.compile(r"^Create$", re.I)),
            page.get_by_text(re.compile(r"^Create$", re.I)).last,
        ):
            try:
                await locator.click(timeout=3500)
                break
            except PlaywrightTimeoutError:
                continue
        else:
            raise CalloutError("Could not find Pump.fun Create menu.")

        for locator in (
            page.get_by_role("menuitem", name=re.compile(r"^Callout$", re.I)),
            page.get_by_text(re.compile(r"^Callout$", re.I)).last,
        ):
            try:
                await locator.click(timeout=3500)
                return
            except PlaywrightTimeoutError:
                continue
        raise CalloutError("Could not find the Pump.fun Callout option.")

    async def _select_coin(self, page: Page, mint: str) -> None:
        search = page.get_by_placeholder(
            re.compile(r"search a coin by name, symbol or mint", re.I)
        ).first
        try:
            await search.wait_for(state="visible", timeout=8000)
        except PlaywrightTimeoutError as exc:
            raise CalloutError("Callout coin-search field was not found.") from exc

        await search.fill(mint)
        await page.wait_for_timeout(800)

        # Prefer an exact mint result. The surrounding row/card normally
        # contains the mint text, but we fall back to a click on exact text.
        exact = page.get_by_text(re.compile(rf"^{re.escape(mint)}$", re.I)).first
        try:
            await exact.click(timeout=5000)
            return
        except PlaywrightTimeoutError:
            pass

        result = page.locator("text=" + mint).first
        try:
            await result.click(timeout=4000)
            return
        except PlaywrightTimeoutError as exc:
            raise CalloutError(
                f"Could not select the exact mint {mint} in the Callout composer."
            ) from exc

    async def _fill_message(self, page: Page, message: str) -> None:
        candidates = [
            page.get_by_placeholder(re.compile(r"callout|conviction|thesis|message|comment", re.I)),
            page.locator("textarea"),
        ]
        for locator in candidates:
            try:
                count = await locator.count()
            except Exception:
                count = 0
            for index in range(count):
                item = locator.nth(index)
                try:
                    if await item.is_visible():
                        await item.fill(message)
                        return
                except Exception:
                    continue
        raise CalloutError("Could not find the Callout message field.")

    async def _post(self, page: Page) -> None:
        candidates = [
            page.get_by_role("button", name=re.compile(r"^(Post|Publish)$", re.I)),
            page.get_by_text(re.compile(r"^(Post|Publish)$", re.I)).last,
        ]
        for locator in candidates:
            try:
                await locator.click(timeout=5000)
                return
            except PlaywrightTimeoutError:
                continue
        raise CalloutError("Could not find the Callout Post button.")

    async def _read_callout_url(self, page: Page, mint: str) -> str:
        deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < deadline:
            url = page.url
            if "/callouts/" in url:
                return url
            await page.wait_for_timeout(500)

            # Some SPA flows render a link after posting instead of navigating.
            links = page.locator('a[href*="/callouts/"]')
            if await links.count():
                href = await links.first.get_attribute("href")
                if href:
                    if href.startswith("/"):
                        return "https://pump.fun" + href
                    return href

        raise CalloutError(
            f"Pump.fun did not expose a callout URL after posting for {mint}. "
            "Check the saved browser profile and selectors in the logs."
        )
