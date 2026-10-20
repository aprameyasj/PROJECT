"""Browser tools.

Two flavours, both behind the same tool interface:

* **`open_url`** — hands the URL to the desktop's real browser (via the action
  backend). This is what you want for "open localhost:3000".
* **Playwright tools** — a headless/automation session JARVIS controls itself:
  navigate, click a selector, fill a form, extract text, screenshot. Used by the
  Phase-4 web agent. Playwright is optional: if it is not installed the tools
  report that clearly instead of crashing.

Install with:  `pip install playwright && playwright install chromium`
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from ..core.safety import check_url
from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool
from .web import absolutise, strip_html

SESSION_KEY = "playwright_session"


class BrowserSession:
    """A single long-lived Playwright browser controlled by JARVIS."""

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self.playwright: Any = None
        self.browser: Any = None
        self.page: Any = None
        self.history: list[str] = []
        self.error: str | None = None

    async def ensure(self) -> bool:
        if self.page is not None:
            return True
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except Exception as exc:  # noqa: BLE001
            self.error = (
                "Playwright is not installed. Run `pip install playwright && playwright install chromium`."
            )
            return False
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=self.headless)
            context = await self.browser.new_context(viewport={"width": 1440, "height": 900})
            self.page = await context.new_page()
            self.error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = f"Could not start Chromium: {exc}. Run `playwright install chromium`."
            await self.close()
            return False

    async def close(self) -> None:
        for closer in (
            lambda: self.page.close() if self.page else None,
            lambda: self.browser.close() if self.browser else None,
            lambda: self.playwright.stop() if self.playwright else None,
        ):
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                pass
        self.page = self.browser = self.playwright = None

    def snapshot(self) -> dict[str, Any]:
        return {"open": self.page is not None, "url": self.history[-1] if self.history else None,
                "headless": self.headless, "visits": len(self.history), "error": self.error}


def _session(ctx: ToolContext) -> BrowserSession:
    session = ctx.services.get(SESSION_KEY)
    if session is None:
        session = BrowserSession(headless=bool(getattr(ctx.settings, "playwright_headless", True)))
        ctx.services[SESSION_KEY] = session
    return session


# ---------------------------------------------------------------------------
# real browser
# ---------------------------------------------------------------------------


@tool(
    permission=Permission.SAFE,
    category=ToolCategory.BROWSER,
    examples=["Open github.com", "Jarvis, open localhost:3000"],
)
def open_url(ctx: ToolContext, url: str, browser: str = "") -> ToolResult:
    """Open a URL in the user's real browser.

    Args:
        url: URL or domain to open ("github.com" becomes "https://github.com").
        browser: Optional browser to use ("chrome", "firefox", "edge"). Defaults to the system browser.
    """
    target = (url or "").strip()
    if not target:
        return ToolResult(ok=False, message="Give me a URL to open.")

    decision = check_url(target)
    if decision.blocked:
        return ToolResult(ok=False, message=decision.reason, data={"url": target})

    full = absolutise(target if "://" in target else ("http://" + target if target.startswith("localhost")
                                                      else "https://" + target))
    ctx.services["last_url"] = full
    info = ctx.backend.open_url(full, browser or None)
    if info.get("ok") is False:
        return ToolResult(ok=False, message=f"Could not open {full}: {info.get('error')}", data=info)
    host = full.split("/")[2] if "://" in full else full
    return ToolResult(ok=True, message=f"Opened {full}", speech=f"Opening {host}.", data=info,
                      simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.BROWSER)
def open_in_default_browser(ctx: ToolContext, url: str) -> ToolResult:
    """Open a URL with the OS default handler (works for mailto:, files, apps).

    Args:
        url: Any URL or URI scheme the OS understands.
    """
    import webbrowser

    decision = check_url(url)
    if decision.blocked:
        return ToolResult(ok=False, message=decision.reason)
    opened = webbrowser.open(url)
    return ToolResult(ok=bool(opened), message=f"Default handler opened {url}" if opened
                      else f"No handler available for {url}", speech="Opened." if opened else "That didn't open.",
                      simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.BROWSER)
def search_and_open(ctx: ToolContext, query: str) -> ToolResult:
    """Search the web and immediately open the best result in the browser.

    Args:
        query: What to look for.
    """
    encoded = query.strip().replace(" ", "+")
    url = f"https://duckduckgo.com/?q={encoded}"
    info = ctx.backend.open_url(url)
    return ToolResult(ok=True, message=f"Opened search results for “{query}”",
                      speech=f"Searching for {query}.", data=info, simulated=ctx.backend.simulated)


# ---------------------------------------------------------------------------
# Playwright automation (Phase 4 — web agent)
# ---------------------------------------------------------------------------


@tool(permission=Permission.SAFE, category=ToolCategory.BROWSER)
async def browser_navigate(ctx: ToolContext, url: str, wait_seconds: float = 1.5) -> ToolResult:
    """Navigate JARVIS's own automation browser to a URL (headless by default).

    Args:
        url: Destination URL.
        wait_seconds: How long to wait for the page to settle.
    """
    decision = check_url(url)
    if decision.blocked:
        return ToolResult(ok=False, message=decision.reason)

    session = _session(ctx)
    if not await session.ensure():
        return ToolResult(ok=False, message=session.error or "Browser unavailable.",
                          data={"session": session.snapshot()})
    target = absolutise(url)
    try:
        await session.page.goto(target, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(min(max(0.0, wait_seconds), 10))
        title = await session.page.title()
        session.history.append(target)
        return ToolResult(ok=True, message=f"Navigated to {target} — “{title}”",
                          speech=f"I opened {title}.",
                          data={"url": target, "title": title, "session": session.snapshot()})
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Navigation failed: {exc}", data={"url": target})


@tool(permission=Permission.SAFE, category=ToolCategory.BROWSER)
async def browser_extract(ctx: ToolContext, selector: str = "body", attribute: str = "text",
                          max_items: int = 20) -> ToolResult:
    """Extract text, links or an attribute from the current automation page.

    Args:
        selector: CSS selector to read (default "body" = the whole page text).
        attribute: "text" for inner text, "href"/"src"/any attribute name to read that attribute.
        max_items: Maximum number of matching elements to return.
    """
    session = _session(ctx)
    if session.page is None:
        return ToolResult(ok=False, message="The automation browser isn't on a page. Use browser_navigate first.")
    try:
        if attribute == "text" and selector in {"body", "", "html"}:
            content = await session.page.content()
            text = strip_html(content)
            return ToolResult(ok=True, message=f"Extracted {len(text)} characters of page text",
                              data={"url": session.page.url, "text": text[:8000], "length": len(text)})

        elements = await session.page.query_selector_all(selector)
        items: list[Any] = []
        for element in elements[: max(1, min(int(max_items), 100))]:
            if attribute == "text":
                items.append((await element.inner_text()).strip())
            else:
                items.append(await element.get_attribute(attribute))
        return ToolResult(ok=True, message=f"{len(items)} element(s) matched `{selector}`",
                          data={"selector": selector, "attribute": attribute, "items": items,
                                "url": session.page.url})
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Extraction failed: {exc}")


@tool(permission=Permission.SAFE, category=ToolCategory.BROWSER)
async def browser_click(ctx: ToolContext, selector: str = "", text: str = "") -> ToolResult:
    """Click an element in the automation browser, by CSS selector or visible text.

    Args:
        selector: CSS selector to click.
        text: Visible text to click (used when selector is empty), e.g. "Sign in".
    """
    session = _session(ctx)
    if session.page is None:
        return ToolResult(ok=False, message="The automation browser isn't on a page. Use browser_navigate first.")
    try:
        if selector:
            await session.page.click(selector, timeout=8000)
            what = selector
        elif text:
            await session.page.get_by_text(text, exact=False).first.click(timeout=8000)
            what = f"“{text}”"
        else:
            return ToolResult(ok=False, message="Give me a selector or the text to click.")
        await asyncio.sleep(0.8)
        return ToolResult(ok=True, message=f"Clicked {what}; now at {session.page.url}",
                          speech="Clicked.", data={"clicked": what, "url": session.page.url})
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Could not click: {exc}")


@tool(permission=Permission.SAFE, category=ToolCategory.BROWSER)
async def browser_type(ctx: ToolContext, selector: str, text: str, submit: bool = False) -> ToolResult:
    """Type into a form field in the automation browser.

    Args:
        selector: CSS selector of the input field.
        text: Text to type.
        submit: Press Enter after typing (submits the form).
    """
    session = _session(ctx)
    if session.page is None:
        return ToolResult(ok=False, message="The automation browser isn't on a page.")
    try:
        await session.page.fill(selector, text, timeout=8000)
        if submit:
            await session.page.press(selector, "Enter")
            await asyncio.sleep(1.0)
        return ToolResult(ok=True, message=f"Typed into `{selector}`" + (" and submitted" if submit else ""),
                          speech="Filled in.", data={"selector": selector, "submitted": submit,
                                                     "url": session.page.url})
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Could not type into {selector}: {exc}")


@tool(permission=Permission.SAFE, category=ToolCategory.BROWSER)
async def browser_screenshot(ctx: ToolContext, full_page: bool = False, note: str = "") -> ToolResult:
    """Screenshot the automation browser page (not the user's desktop).

    Args:
        full_page: Capture the entire scrollable page instead of the viewport.
        note: Optional label stored with the artifact.
    """
    session = _session(ctx)
    if session.page is None:
        return ToolResult(ok=False, message="The automation browser isn't on a page.")
    directory = Path(ctx.settings.screenshots_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"page-{int(time.time())}.png"
    try:
        await session.page.screenshot(path=str(path), full_page=full_page)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Screenshot failed: {exc}")
    if ctx.db is not None:
        ctx.db.add_artifact("screenshot", str(path), {"note": note, "url": session.page.url, "kind": "browser"})
    return ToolResult(ok=True, message=f"Saved {path}", speech="Captured the page.",
                      data={"path": str(path), "url": session.page.url},
                      artifacts=[{"type": "image", "path": str(path)}])


@tool(permission=Permission.SAFE, category=ToolCategory.BROWSER)
async def browser_status(ctx: ToolContext) -> ToolResult:
    """Report the state of JARVIS's automation browser."""
    session = _session(ctx)
    return ToolResult(ok=True, message="Automation browser state", data=session.snapshot(),
                      speech="The automation browser is " + ("open." if session.page else "not running."))


@tool(permission=Permission.SAFE, category=ToolCategory.BROWSER)
async def browser_close(ctx: ToolContext) -> ToolResult:
    """Close JARVIS's automation browser session and free its memory."""
    session = _session(ctx)
    await session.close()
    return ToolResult(ok=True, message="Automation browser closed.", speech="Closed the browser session.")
