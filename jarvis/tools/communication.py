"""Communication tools — anything that sends data *off* the machine.

These are all Level 2 (confirmation) by design: JARVIS will read the message
back to you and wait for a "yes" before it leaves the building. Nothing here is
enabled unless you configure a provider (SMTP for mail, a webhook URL for
notifications), so there is no way for the model to exfiltrate data by accident.

Configure in `.env`:

    JARVIS_SMTP_HOST=smtp.gmail.com
    JARVIS_SMTP_PORT=587
    JARVIS_SMTP_USER=you@gmail.com
    JARVIS_SMTP_PASSWORD=app-password
    JARVIS_MAIL_FROM=you@gmail.com
    JARVIS_WEBHOOK_URL=https://hooks.example.com/...      # optional
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool


def _smtp_config() -> dict[str, Any]:
    return {
        "host": os.environ.get("JARVIS_SMTP_HOST", ""),
        "port": int(os.environ.get("JARVIS_SMTP_PORT", "587")),
        "user": os.environ.get("JARVIS_SMTP_USER", ""),
        "password": os.environ.get("JARVIS_SMTP_PASSWORD", ""),
        "sender": os.environ.get("JARVIS_MAIL_FROM", "") or os.environ.get("JARVIS_SMTP_USER", ""),
        "starttls": os.environ.get("JARVIS_SMTP_STARTTLS", "1") not in {"0", "false", "no"},
    }


def _drafts_dir(ctx: ToolContext) -> Path:
    directory = Path(ctx.settings.home) / "drafts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@tool(permission=Permission.CONFIRM, category=ToolCategory.PRODUCTIVITY, mutating=True)
def draft_email(ctx: ToolContext, to: str, subject: str, body: str = "", cc: str = "") -> ToolResult:
    """Write an email draft to disk without sending it (no confirmation needed to write).

    Args:
        to: Recipient address.
        subject: Subject line.
        body: Message body.
        cc: Optional CC address.
    """
    if not to or "@" not in to:
        return ToolResult(ok=False, message="That doesn't look like an email address.",
                          speech="Who should I send that to?")
    path = _drafts_dir(ctx) / f"{abs(hash((to, subject))) % 10**8:08d}.json"
    payload = {"to": to, "cc": cc, "subject": subject, "body": body, "path": str(path)}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return ToolResult(ok=True, message=f"Draft saved to {path}",
                      speech=f"I've written the draft to {to}. Want me to send it?",
                      data={"draft": payload})


@tool(permission=Permission.CONFIRM, category=ToolCategory.PRODUCTIVITY, mutating=True)
def send_email(ctx: ToolContext, to: str, subject: str, body: str = "", cc: str = "") -> ToolResult:
    """Send an email over SMTP. Always asks for confirmation first.

    Args:
        to: Recipient address.
        subject: Subject line.
        body: Message body.
        cc: Optional CC address.
    """
    if not to or "@" not in to:
        return ToolResult(ok=False, message="That doesn't look like an email address.")

    config = _smtp_config()
    if not config["host"] or not config["user"]:
        return ToolResult(
            ok=False,
            message="Email is not configured. Set JARVIS_SMTP_HOST / JARVIS_SMTP_USER / "
                    "JARVIS_SMTP_PASSWORD / JARVIS_MAIL_FROM in your .env file.",
            speech="I can't send email yet — SMTP isn't configured. I saved it as a draft instead.",
            data={"configured": False, "draft": str(_save_draft(ctx, to, subject, body, cc))},
        )

    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=25) as server:
            if config["starttls"]:
                server.starttls(context=ssl.create_default_context())
            server.login(config["user"], config["password"])
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Sending failed: {exc}", speech="The email didn't send.",
                          data={"error": str(exc), "to": to})

    return ToolResult(ok=True, message=f"Email sent to {to}: {subject}", speech=f"Sent to {to}.",
                      data={"to": to, "cc": cc, "subject": subject, "via": config["host"]})


def _save_draft(ctx: ToolContext, to: str, subject: str, body: str, cc: str) -> Path:
    path = _drafts_dir(ctx) / f"{abs(hash((to, subject))) % 10**8:08d}.json"
    path.write_text(json.dumps({"to": to, "cc": cc, "subject": subject, "body": body}, indent=2),
                    encoding="utf-8")
    return path


@tool(permission=Permission.CONFIRM, category=ToolCategory.PRODUCTIVITY, mutating=True)
def post_webhook(ctx: ToolContext, message: str, event: str = "jarvis") -> ToolResult:
    """POST a JSON message to your configured webhook (Slack/Discord/ntfy/IFTTT).

    Args:
        message: Text payload to send.
        event: Event name included in the JSON body.
    """
    url = os.environ.get("JARVIS_WEBHOOK_URL", "")
    if not url:
        return ToolResult(ok=False, message="No webhook configured (set JARVIS_WEBHOOK_URL).",
                          speech="I don't have a webhook to post to.")
    payload = {"text": message, "content": message, "event": event, "source": "jarvis"}
    try:
        response = httpx.post(url, json=payload, timeout=15)
        ok = response.status_code < 400
        return ToolResult(ok=ok, message=f"Webhook responded {response.status_code}",
                          speech="Posted to your webhook." if ok else "The webhook rejected that.",
                          data={"status": response.status_code, "body": response.text[:400]})
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Webhook failed: {exc}", speech="Couldn't reach the webhook.")


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def list_drafts(ctx: ToolContext, limit: int = 10) -> ToolResult:
    """List the email drafts JARVIS has written but not sent.

    Args:
        limit: Maximum drafts to return.
    """
    directory = _drafts_dir(ctx)
    drafts = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, limit)]:
        try:
            drafts.append(json.loads(path.read_text(encoding="utf-8")) | {"file": str(path)})
        except Exception:  # noqa: BLE001
            continue
    if not drafts:
        return ToolResult(ok=True, message="No drafts.", speech="You have no drafts.", data={"drafts": []})
    return ToolResult(ok=True, message=f"{len(drafts)} draft(s)",
                      speech=f"You have {len(drafts)} unsent draft" + ("s." if len(drafts) != 1 else "."),
                      data={"drafts": drafts})


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def communication_status() -> ToolResult:
    """Report which outbound channels are configured (email, webhook)."""
    config = _smtp_config()
    data = {
        "email_configured": bool(config["host"] and config["user"]),
        "smtp_host": config["host"] or None,
        "mail_from": config["sender"] or None,
        "webhook_configured": bool(os.environ.get("JARVIS_WEBHOOK_URL")),
        "permission_level": "L2 — every send asks for confirmation",
    }
    ready = [name for name, ok in (("email", data["email_configured"]),
                                   ("webhook", data["webhook_configured"])) if ok]
    speech = ("I can send via " + " and ".join(ready) + ", but I'll always ask first.") if ready else \
        "No outbound channels are configured yet, so nothing can leave your machine."
    return ToolResult(ok=True, message="; ".join(f"{k}={v}" for k, v in data.items()), speech=speech, data=data)
