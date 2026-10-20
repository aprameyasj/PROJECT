"""Vision tools (Phase 5): see the screen, find a UI element, click it.

Pipeline:

    take_screenshot -> vision model (or OCR fallback) -> element box -> click

If no multimodal model is configured, the tools degrade gracefully: they use
the desktop's window/tab metadata, plus OCR when `pytesseract` and Tesseract
are installed. In a sandbox they operate on the simulated screenshot so the
whole flow is still exercisable end to end.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx

from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool

VISION_SYSTEM_PROMPT = """\
You are the vision module of a desktop agent. You are given a screenshot and a task.
Answer with strict JSON only — no prose, no code fences.

For LOCATE tasks return:
{"found": true, "label": "...", "x": 0, "y": 0, "width": 0, "height": 0,
 "confidence": 0.0, "reason": "..."}
Coordinates are in the *original image pixel space*, with (0,0) at the top-left,
and x/y are the centre of the element.

For DESCRIBE tasks return:
{"summary": "...", "active_app": "...", "elements": [{"label": "...", "type": "button|input|link|text|icon",
 "x": 0, "y": 0}], "suggested_actions": ["..."]}
"""


def _encode_image(path: str | Path) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")


async def _vision_call(settings: Any, image_path: str | Path, task: str) -> dict[str, Any] | None:
    """Ask a multimodal model about a screenshot. Returns parsed JSON or None."""
    if not getattr(settings, "llm_available", False):
        return None
    model = getattr(settings, "vision_model", None) or getattr(settings, "llm_model", "gpt-4o-mini")
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 900,
        "messages": [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": task},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{_encode_image(image_path)}", "detail": "high"}},
                ],
            },
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{str(settings.llm_base_url).rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"] or "{}"
    except Exception:  # noqa: BLE001
        return None
    return _loads_lenient(content)


def _loads_lenient(content: str) -> dict[str, Any]:
    text = content.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"raw": content}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else {"raw": content}
            except json.JSONDecodeError:
                pass
    return {"raw": content}


def _ocr_fallback(image_path: str | Path) -> list[dict[str, Any]] | None:
    """Best-effort OCR with pytesseract (returns words with bounding boxes)."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        data = pytesseract.image_to_data(Image.open(str(image_path)), output_type=pytesseract.Output.DICT)
    except Exception:  # noqa: BLE001
        return None
    words: list[dict[str, Any]] = []
    for index, text in enumerate(data.get("text", [])):
        text = (text or "").strip()
        if not text or int(data["conf"][index]) < 45:
            continue
        left, top = int(data["left"][index]), int(data["top"][index])
        width, height = int(data["width"][index]), int(data["height"][index])
        words.append({
            "label": text,
            "x": left + width // 2,
            "y": top + height // 2,
            "left": left, "top": top, "width": width, "height": height,
            "confidence": int(data["conf"][index]) / 100,
        })
    return words or None


def _match_ocr(words: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    needle = label.lower().strip()
    tokens = needle.split()
    exact = [w for w in words if w["label"].lower() == needle]
    if exact:
        return max(exact, key=lambda w: w.get("confidence", 0))
    starts = [w for w in words if w["label"].lower().startswith(needle) or needle in w["label"].lower()]
    if starts:
        return max(starts, key=lambda w: w.get("confidence", 0))
    if len(tokens) > 1:  # adjacent-word phrase
        for index in range(len(words) - len(tokens) + 1):
            chunk = words[index:index + len(tokens)]
            if " ".join(w["label"].lower() for w in chunk) == needle:
                return {"label": needle, "x": (chunk[0]["x"] + chunk[-1]["x"]) // 2,
                        "y": chunk[0]["y"], "confidence": 0.6, "width": chunk[-1]["left"] + chunk[-1]["width"]
                        - chunk[0]["left"], "height": chunk[0]["height"]}
    return None


async def _capture(ctx: ToolContext) -> dict[str, Any]:
    return ctx.backend.screenshot()


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


@tool(permission=Permission.SAFE, category=ToolCategory.VISION,
      examples=["What's on my screen?", "Describe the screen"])
async def describe_screen(ctx: ToolContext, focus: str = "") -> ToolResult:
    """Look at the screen and describe what is on it.

    Args:
        focus: Optional question about the screen, e.g. "is the build failing?".
    """
    shot = await _capture(ctx)
    path = shot.get("path", "")
    if ctx.db is not None and path:
        ctx.db.add_artifact("screenshot", path, {"tool": "describe_screen", "simulated": shot.get("simulated")})

    task = "DESCRIBE this screenshot." + (f" Pay particular attention to: {focus}." if focus else "")
    analysis = await _vision_call(ctx.settings, path, task) if path else None

    windows = ctx.backend.list_windows()
    if not analysis:
        # no vision model: fall back to what the OS/backend can tell us
        words = _ocr_fallback(path) if path else None
        data: dict[str, Any] = {
            "screenshot": shot,
            "windows": windows,
            "ocr_available": bool(words),
            "vision_model": False,
        }
        if words:
            data["text_on_screen"] = " ".join(w["label"] for w in words[:200])
        summary = (
            f"I can see {len(windows)} window(s)"
            + (f": {', '.join(str(w.get('title', '?')) for w in windows[:3])}" if windows else "")
            + (". No multimodal model is configured, so this is from window metadata and OCR."
               if not words else ". I also read the text on screen via OCR.")
        )
        return ToolResult(ok=True, message=summary, speech=summary, data=data,
                          artifacts=[{"type": "image", "path": path}], simulated=bool(shot.get("simulated")))

    summary = analysis.get("summary") or "I can see your screen."
    return ToolResult(
        ok=True,
        message=summary,
        speech=summary,
        data={"analysis": analysis, "screenshot": shot, "windows": windows, "vision_model": True},
        artifacts=[{"type": "image", "path": path}],
        simulated=bool(shot.get("simulated")),
    )


@tool(permission=Permission.SAFE, category=ToolCategory.VISION)
async def find_on_screen(ctx: ToolContext, label: str, take_screenshot: bool = True) -> ToolResult:
    """Locate a UI element (button, link, field, icon) on screen and return its coordinates.

    Args:
        label: Visible text or description, e.g. "Login", "Submit", "search box".
        take_screenshot: Capture a fresh screenshot first (set false to reuse the last one).
    """
    if not label.strip():
        return ToolResult(ok=False, message="Tell me what to look for.")

    if take_screenshot:
        shot = await _capture(ctx)
        path = shot.get("path", "")
    else:
        artifacts = ctx.db.list_artifacts("screenshot", limit=1) if ctx.db is not None else []
        path = artifacts[0]["path"] if artifacts else ""
        shot = {"path": path, "simulated": bool(artifacts and artifacts[0].get("meta", {}).get("simulated"))}

    if not path or not Path(path).exists():
        return ToolResult(ok=False, message="I couldn't capture the screen.", data={"label": label})

    location = await _vision_call(
        ctx.settings, path,
        f"LOCATE the UI element described as “{label}”. If it is not visible, return found=false "
        "with a short reason.",
    )
    if location and location.get("found"):
        width, height = ctx.backend.screen_size()
        scale = width / (shot.get("width") or width) if shot.get("width") else 1.0
        return ToolResult(
            ok=True,
            message=f"Found “{label}” at ({location['x']}, {location['y']})",
            speech=f"I can see {label}.",
            data={"label": label, "x": int(location["x"] * scale), "y": int(location["y"] * scale),
                  "box": location, "source": "vision", "screenshot": shot},
        )

    words = _ocr_fallback(path)
    if words:
        match = _match_ocr(words, label)
        if match:
            return ToolResult(ok=True, message=f"Found “{label}” at ({match['x']}, {match['y']}) via OCR",
                              speech=f"I can see {label}.",
                              data={"label": label, "x": match["x"], "y": match["y"], "box": match,
                                    "source": "ocr", "screenshot": shot})

    reason = (location or {}).get("reason") or "no vision model or OCR available"
    return ToolResult(ok=False, message=f"“{label}” is not on screen ({reason}).",
                      speech=f"I can't find {label} on the screen.",
                      data={"label": label, "reason": reason, "screenshot": shot})


@tool(permission=Permission.SAFE, category=ToolCategory.VISION,
      examples=["Jarvis, click the login button."])
async def click_element(ctx: ToolContext, label: str, button: str = "left", double: bool = False) -> ToolResult:
    """Find a UI element on screen by its text and click it.

    Args:
        label: Visible text of the element, e.g. "Login" or "Save changes".
        button: "left" or "right".
        double: Double-click instead of a single click.
    """
    found = await find_on_screen(ctx, label=label)
    if not found.ok or found.data is None or "x" not in found.data:
        return ToolResult(ok=False, message=found.message, speech=found.spoken, data=found.data)

    x, y = int(found.data["x"]), int(found.data["y"])
    message = ctx.backend.click(x=x, y=y, button=button, clicks=2 if double else 1)
    return ToolResult(
        ok=True,
        message=f"Clicked “{label}” at ({x}, {y}) — {message}",
        speech=f"Clicked {label}.",
        data={"label": label, "x": x, "y": y, "source": found.data.get("source"),
              "screenshot": found.data.get("screenshot")},
        simulated=ctx.backend.simulated,
    )


@tool(permission=Permission.SAFE, category=ToolCategory.VISION)
async def read_screen_text(ctx: ToolContext, max_chars: int = 3000) -> ToolResult:
    """Read all visible text on the screen (OCR). Useful for forms and error messages.

    Args:
        max_chars: Maximum characters to return.
    """
    shot = await _capture(ctx)
    path = shot.get("path", "")
    words = _ocr_fallback(path) if path else None
    if not words:
        analysis = await _vision_call(ctx.settings, path, "DESCRIBE this screenshot, listing all visible text.")
        if analysis and analysis.get("summary"):
            return ToolResult(ok=True, message="Extracted text via the vision model",
                              speech="I read the screen.",
                              data={"text": analysis["summary"][:max_chars], "source": "vision",
                                    "screenshot": shot})
        return ToolResult(ok=False, message="OCR is unavailable. Install pytesseract + tesseract-ocr, "
                                            "or configure a vision model.",
                          speech="I can't read the screen text without OCR or a vision model.",
                          data={"screenshot": shot})

    text = " ".join(w["label"] for w in words)[:max_chars]
    return ToolResult(ok=True, message=f"Read {len(words)} words from the screen",
                      speech="I read the text on your screen.",
                      data={"text": text, "words": len(words), "source": "ocr", "screenshot": shot,
                            "boxes": words[:50]})
