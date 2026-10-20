"""OpenAI-compatible chat completions provider.

Talks to anything that implements `/chat/completions` with function calling:
OpenAI, Azure-flavoured gateways, OpenRouter, Groq, Together, Fireworks, a local
Ollama (`http://localhost:11434/v1`) or LM Studio. Set `OPENAI_BASE_URL` +
`OPENAI_API_KEY` (or `JARVIS_LLM_API_KEY`) and `JARVIS_LLM_MODEL`.

Implemented directly on `httpx` so the SDK is not a hard dependency.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import httpx

from ..core.types import Message, ToolCall
from .base import Decision
from .prompts import build_planner_prompt, build_response_prompt


class OpenAIBrain:
    name = "openai"

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.api_key = settings.llm_api_key
        self.base_url = (settings.llm_base_url or "").rstrip("/")
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.timeout = settings.llm_timeout
        self.available = bool(self.api_key) and bool(self.base_url)
        self.last_error: str | None = None
        self.calls = 0

    # ------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "available": self.available,
            "calls": self.calls,
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------
    async def _post(self, payload: dict[str, Any], *, retries: int = 2) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("No LLM API key configured (set OPENAI_API_KEY).")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)
                self.calls += 1
                if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                    await asyncio.sleep(0.6 * (2 ** attempt) + random.uniform(0, 0.3))
                    continue
                if response.status_code >= 400:
                    detail = response.text[:600]
                    raise RuntimeError(f"LLM HTTP {response.status_code}: {detail}")
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(0.6 * (2 ** attempt))
                    continue
                raise RuntimeError(f"LLM request failed: {exc}") from exc
        raise RuntimeError(f"LLM request failed: {last_error}")

    # ------------------------------------------------------------------
    async def decide(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        catalogue: str = "",
        context: dict[str, Any] | None = None,
        **_: Any,
    ) -> Decision:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [m.to_openai() for m in messages],
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True

        try:
            data = await self._post(payload)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return Decision(text="", error=str(exc), provider=self.name, model=self.model)

        choices = data.get("choices") or []
        if not choices:
            self.last_error = "empty completion"
            return Decision(error="The model returned no choices.", provider=self.name, model=self.model)

        message = choices[0].get("message") or {}
        finish = choices[0].get("finish_reason") or ""
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments = function.get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    parsed_args = json.loads(arguments) if arguments.strip() else {}
                except json.JSONDecodeError:
                    parsed_args = {"_raw": arguments}
            else:
                parsed_args = dict(arguments)
            if not isinstance(parsed_args, dict):
                parsed_args = {"value": parsed_args}
            calls.append(
                ToolCall(
                    name=function.get("name") or "",
                    arguments=parsed_args,
                    id=raw.get("id") or f"call_{len(calls)}",
                    source="llm",
                )
            )

        text = message.get("content")
        if isinstance(text, list):  # some providers return content parts
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))

        decision = Decision(
            text=(text or "").strip(),
            tool_calls=[c for c in calls if c.name],
            intent=_guess_intent(calls),
            provider=self.name,
            model=data.get("model") or self.model,
            finish_reason=finish,
            usage=data.get("usage") or {},
            raw=message,
        )
        self.last_error = None
        return decision

    # ------------------------------------------------------------------
    async def plan(self, goal: str, *, catalogue: str, context: str = "", **_: Any) -> dict[str, Any]:
        prompt = build_planner_prompt(settings=self.settings, goal=goal, catalogue=catalogue, context=context)
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": "You output strict JSON only, no prose, no code fences."},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            data = await self._post(payload)
            content = data["choices"][0]["message"]["content"] or "{}"
            return _loads_lenient(content)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return {"title": goal, "steps": [], "error": str(exc)}

    async def summarise(self, *, user_text: str, observations: list[str], **_: Any) -> str:
        prompt = build_response_prompt(
            settings=self.settings, user_text=user_text, observations=observations
        )
        payload = {
            "model": self.model,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": f"You are {self.settings.agent_name}. Reply in plain spoken text."},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            data = await self._post(payload)
            return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return observations[-1] if observations else "Done."

    async def chat(self, messages: list[Message]) -> str:
        """Plain conversational completion (no tools) — used for small talk."""
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [m.to_openai() for m in messages],
        }
        try:
            data = await self._post(payload)
            return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return ""

    # ------------------------------------------------------------------
    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm", **_: Any) -> str:
        """Whisper speech-to-text through the same API key."""
        url = f"{self.base_url}/audio/transcriptions"
        data = {"model": self.settings.stt_model, "language": "en"}
        files = {"file": (filename, audio_bytes, "application/octet-stream")}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=headers, data=data, files=files)
        if response.status_code >= 400:
            raise RuntimeError(f"STT HTTP {response.status_code}: {response.text[:300]}")
        return (response.json().get("text") or "").strip()


def _guess_intent(calls: list[ToolCall]) -> str:
    return calls[0].name if calls else ""


def _loads_lenient(content: str) -> dict[str, Any]:
    """Parse JSON that a model may have wrapped in prose or code fences."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"steps": []}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
                return parsed if isinstance(parsed, dict) else {"steps": []}
            except json.JSONDecodeError:
                pass
    return {"steps": [], "error": "could not parse planner JSON", "raw": content[:500]}


__all__ = ["OpenAIBrain"]
