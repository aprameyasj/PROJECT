"""Web tools: search, read pages, download files.

Search providers, in order of preference:

1. **Serper** (Google results) if `SERPER_API_KEY` is set
2. **Brave Search** if `BRAVE_SEARCH_API_KEY` is set
3. **DuckDuckGo** — no key required, used by default

`search_web` can also return a short *answer*: it merges the DuckDuckGo instant
answer with the top result snippets, which is what makes "Jarvis, what is FastAPI?"
work even in offline mode.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from ..core.safety import check_url
from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 JARVIS/0.1"
)

TAG_RE = re.compile(r"<(script|style|noscript|svg|head)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
ANY_TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def strip_html(raw: str) -> str:
    text = TAG_RE.sub(" ", raw)
    text = ANY_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = WS_RE.sub(" ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def absolutise(url: str, base: str = "") -> str:
    if not url:
        return url
    if url.startswith("//"):
        return "https:" + url
    if base and not re.match(r"^[a-z][a-z0-9+.-]*://", url):
        return urllib.parse.urljoin(base, url)
    return url


async def _serper(query: str, api_key: str, limit: int, timeout: float) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": limit},
        )
        response.raise_for_status()
        data = response.json()
    results = []
    for item in (data.get("organic") or [])[:limit]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "position": item.get("position"),
                "source": "google/serper",
            }
        )
    box = data.get("answerBox") or {}
    if box:
        answer = box.get("answer") or box.get("snippet") or box.get("title")
        if answer:
            results.insert(0, {"title": "Answer box", "url": box.get("link", ""), "snippet": str(answer),
                               "source": "google/answerbox"})
    return results


async def _brave(query: str, api_key: str, limit: int, timeout: float) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            params={"q": query, "count": min(limit, 20)},
        )
        response.raise_for_status()
        data = response.json()
    return [
        {"title": item.get("title", ""), "url": item.get("url", ""),
         "snippet": item.get("description", ""), "source": "brave"}
        for item in (data.get("web", {}) or {}).get("results", [])[:limit]
    ]


async def _duckduckgo(query: str, limit: int, timeout: float) -> list[dict[str, Any]]:
    """Scrape the key-free DuckDuckGo HTML endpoint."""
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as client:
        response = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        if response.status_code >= 400:
            response = await client.get("https://lite.duckduckgo.com/lite/", params={"q": query})
        body = response.text

    blocks = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'(?:<a[^>]+class="result__snippet"[^>]*>(.*?)</a>)?',
        body,
        re.DOTALL,
    )
    for href, title, snippet in blocks[:limit]:
        url = urllib.parse.unquote(href)
        if "uddg=" in url:
            url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("uddg", [url])[0]
        results.append(
            {
                "title": strip_html(title),
                "url": absolutise(url),
                "snippet": strip_html(snippet or ""),
                "source": "duckduckgo",
            }
        )

    if not results:  # lite endpoint markup
        lite = re.findall(r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.DOTALL)
        for href, title in lite[:limit]:
            results.append({"title": strip_html(title), "url": absolutise(urllib.parse.unquote(href)),
                            "snippet": "", "source": "duckduckgo-lite"})
    return results


async def _bing(query: str, limit: int, timeout: float) -> list[dict[str, Any]]:
    """Key-free HTML scrape of Bing — a fallback when DuckDuckGo is unreachable."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get("https://www.bing.com/search", params={"q": query, "count": limit})
        response.raise_for_status()
        body = response.text
    results = []
    for block in re.findall(r'<li class="b_algo".*?</li>', body, re.DOTALL)[:limit]:
        link = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not link:
            continue
        snippet = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        results.append({
            "title": strip_html(link.group(2)),
            "url": absolutise(link.group(1)),
            "snippet": strip_html(snippet.group(1)) if snippet else "",
            "source": "bing",
        })
    return results


async def _wikipedia(query: str, limit: int, timeout: float) -> list[dict[str, Any]]:
    """Last-resort knowledge source: the Wikipedia search + summary API."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query, "srlimit": limit, "format": "json"},
        )
        response.raise_for_status()
        hits = (response.json().get("query", {}) or {}).get("search", [])
        results = []
        for hit in hits[:limit]:
            title = hit.get("title", "")
            results.append({
                "title": title,
                "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
                "snippet": strip_html(hit.get("snippet", "")),
                "source": "wikipedia",
            })
    return results


async def _ddg_instant_answer(query: str, timeout: float) -> dict[str, Any] | None:
    """DuckDuckGo Zero-click API: definitions, abstracts, related topics."""
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            )
            data = response.json()
    except Exception:  # noqa: BLE001
        return None
    abstract = (data.get("AbstractText") or "").strip()
    answer = (data.get("Answer") or "").strip()
    definition = (data.get("Definition") or "").strip()
    related = [
        {"title": strip_html(t.get("Text", "")), "url": t.get("FirstURL", "")}
        for t in (data.get("RelatedTopics") or [])[:5]
        if isinstance(t, dict) and t.get("Text")
    ]
    if not (abstract or answer or definition or related):
        return None
    return {
        "heading": data.get("Heading", ""),
        "abstract": abstract,
        "answer": answer,
        "definition": definition,
        "abstract_url": data.get("AbstractURL", ""),
        "related": related,
        "source": "duckduckgo-instant",
    }


def _compose_answer(query: str, instant: dict[str, Any] | None, results: list[dict[str, Any]]) -> str:
    """Extractive answer used when there is no LLM to summarise."""
    if instant:
        for key in ("answer", "abstract", "definition"):
            if instant.get(key):
                text = str(instant[key]).strip()
                heading = instant.get("heading")
                prefix = f"{heading}: " if heading else ""
                return f"{prefix}{text}"[:600]
    for result in results[:3]:
        snippet = (result.get("snippet") or "").strip()
        if len(snippet) > 60:
            return f"{result.get('title', '')}: {snippet}"[:600]
    if results:
        return f"Top result for “{query}”: {results[0].get('title', '')} — {results[0].get('url', '')}"
    return ""


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


@tool(
    permission=Permission.SAFE,
    category=ToolCategory.WEB,
    examples=["Search the web for FastAPI tutorials", "Jarvis, what is Python?"],
)
async def search_web(ctx: ToolContext, query: str, max_results: int = 5, open_top: bool = False,
                     answer: bool = False) -> ToolResult:
    """Search the web and return the top results.

    Args:
        query: What to search for.
        max_results: How many results to return (1-10).
        open_top: Also open the best result in the browser.
        answer: Include a short direct answer extracted from the results.
    """
    query = (query or "").strip()
    if not query:
        return ToolResult(ok=False, message="I need something to search for.")

    limit = max(1, min(int(max_results), 10))
    timeout = float(getattr(ctx.settings, "web_timeout", 12.0))
    provider = getattr(ctx.settings, "search_provider", "duckduckgo")
    api_key = getattr(ctx.settings, "search_api_key", "")
    results: list[dict[str, Any]] = []
    used = provider
    errors: list[str] = []

    # provider chain: paid API first (if configured), then the key-free sources
    chain: list[tuple[str, Any]] = []
    if provider in {"serper", "google"} and api_key:
        chain.append(("google/serper", lambda: _serper(query, api_key, limit, timeout)))
    if provider == "brave" and api_key:
        chain.append(("brave", lambda: _brave(query, api_key, limit, timeout)))
    chain += [
        ("duckduckgo", lambda: _duckduckgo(query, limit, timeout)),
        ("bing", lambda: _bing(query, limit, timeout)),
        ("wikipedia", lambda: _wikipedia(query, limit, timeout)),
    ]
    if provider == "duckduckgo":
        pass  # default order
    elif provider not in {"serper", "google", "brave"}:
        chain.sort(key=lambda item: 0 if item[0] == provider else 1)

    for name, attempt in chain:
        try:
            found = await attempt()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}")
            continue
        if found:
            results, used = found, name
            break
        errors.append(f"{name}: no results")

    error = "; ".join(errors) if not results else None

    instant = await _ddg_instant_answer(query, timeout) if not error else None
    composed = _compose_answer(query, instant, results) if (answer or not ctx.settings.llm_available) else ""

    data: dict[str, Any] = {
        "query": query,
        "provider": used,
        "count": len(results),
        "results": results[:limit],
        "instant": instant,
    }
    if composed:
        data["answer"] = composed

    if open_top and results:
        ctx.backend.open_url(results[0]["url"])
        data["opened"] = results[0]["url"]

    if not results:
        detail = error or "no results"
        hint = (" Outbound web access looks blocked in this environment (sandbox/proxy) — "
                "on your own machine this tool reaches the internet normally."
                if "ConnectError" in detail or "Timeout" in detail or "SSL" in detail else "")
        return ToolResult(ok=False, message=f"Web search failed: {detail}.{hint}",
                          data={"query": query, "error": detail, "tried": errors},
                          speech="Sorry, the web search didn't go through — looks like I have no internet "
                                 "from here.")

    speech = composed or f"I found {len(results)} results for {query}. The top one is {results[0].get('title', '')}."
    return ToolResult(
        ok=True,
        message=f"{len(results)} results for “{query}” via {used}",
        speech=speech[:300],
        data=data,
        error=error,
    )


@tool(permission=Permission.SAFE, category=ToolCategory.WEB)
async def read_page(ctx: ToolContext, url: str = "", max_chars: int = 4000) -> ToolResult:
    """Fetch a web page and return its readable text content.

    Args:
        url: The page URL. Leave empty to re-read the last opened URL.
        max_chars: Maximum characters of text to return (default 4000).
    """
    target = (url or "").strip() or str(ctx.service("last_url", ""))
    if not target:
        return ToolResult(ok=False, message="Give me a URL to read.")

    decision = check_url(target)
    if decision.blocked:
        return ToolResult(ok=False, message=decision.reason, data={"url": target})

    timeout = float(getattr(ctx.settings, "web_timeout", 12.0))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(absolutise(target))
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                return ToolResult(ok=False, message=f"`{target}` is not a text page ({content_type}).",
                                  data={"url": str(response.url), "content_type": content_type})
            text = strip_html(response.text)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Could not read {target}: {exc}", data={"url": target})

    ctx.services["last_url"] = str(response.url)
    limited = text[: max(500, int(max_chars))]
    title_match = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.IGNORECASE | re.DOTALL)
    title = html.unescape(title_match.group(1)).strip() if title_match else str(response.url)
    links = [absolutise(href, str(response.url)) for href in re.findall(r'href="([^"]+)"', response.text)][:25]

    return ToolResult(
        ok=True,
        message=f"Read {title} ({len(text)} characters)",
        speech=f"I read {title}. " + (limited[:220].replace("\n", " ") + "…" if limited else ""),
        data={"url": str(response.url), "title": title, "length": len(text),
              "text": limited, "truncated": len(text) > len(limited), "links": links[:15]},
    )


@tool(permission=Permission.CONFIRM, category=ToolCategory.WEB, mutating=True)
async def download_file(ctx: ToolContext, url: str, destination: str = "") -> ToolResult:
    """Download a file from a URL into the JARVIS downloads folder.

    Args:
        url: The file URL to download.
        destination: Optional file name or path. Defaults to the URL's file name.
    """
    decision = check_url(url)
    if decision.blocked:
        return ToolResult(ok=False, message=decision.reason)

    downloads = Path(ctx.settings.home) / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    name = destination.strip() or Path(urllib.parse.urlparse(url).path).name or "download.bin"
    target = (downloads / Path(name).name).resolve()

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(target, "wb") as handle:
                    total = 0
                    async for chunk in response.aiter_bytes(65536):
                        handle.write(chunk)
                        total += len(chunk)
                        if total > 200 * 1024 * 1024:
                            raise RuntimeError("download exceeds the 200 MB safety cap")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Download failed: {exc}", data={"url": url})

    return ToolResult(ok=True, message=f"Saved {target} ({total // 1024} KB)",
                      speech=f"Downloaded {target.name}.",
                      data={"path": str(target), "bytes": total}, artifacts=[{"type": "file", "path": str(target)}])


@tool(permission=Permission.SAFE, category=ToolCategory.WEB)
async def check_site(ctx: ToolContext, url: str) -> ToolResult:
    """Check whether a website or local dev server is reachable, and how fast.

    Args:
        url: URL to probe (e.g. http://localhost:3000).
    """
    import time

    target = absolutise((url or "").strip())
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(target)
        elapsed = int((time.perf_counter() - started) * 1000)
        up = response.status_code < 500
        return ToolResult(
            ok=True,
            message=f"{target} responded {response.status_code} in {elapsed} ms",
            speech=f"{target.split('/')[2] if '://' in target else target} is "
                   f"{'up' if up else 'returning errors'}, status {response.status_code}.",
            data={"url": target, "status": response.status_code, "ms": elapsed, "up": up,
                  "content_type": response.headers.get("content-type", "")},
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        return ToolResult(ok=False, message=f"{target} is unreachable: {exc}",
                          speech="That site isn't responding.",
                          data={"url": target, "error": str(exc), "ms": elapsed, "up": False})
