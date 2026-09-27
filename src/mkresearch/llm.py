from __future__ import annotations

import os
import re

import httpx

from mkresearch.cooldown import Guard, LimitReached, limit_kind

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class LlmError(RuntimeError):
    pass


def parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise LlmError("LLM không trả về JSON")
    import json

    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise LlmError("LLM JSON không phải object")
    return payload


class LlmClient:
    def __init__(self, http: httpx.Client | None = None, guard: Guard | None = None) -> None:
        self.http = http or httpx.Client(timeout=60)
        self.guard = guard or Guard()
        self.gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        self.last_model = ""

    def available(self) -> bool:
        return bool(self.gemini_key or self.groq_key)

    def complete(self, prompt: str) -> str:
        errors: list[str] = []
        if self.gemini_key and not self.guard.active("gemini"):
            for model in _gemini_models():
                try:
                    text = self._gemini(prompt, model)
                    self.last_model = model
                    return text
                except Exception as exc:
                    errors.append(f"{model}: {_public_error(exc)}")
                    if self._trip("gemini", exc):
                        break
        if self.groq_key and not self.guard.active("groq"):
            model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
            try:
                text = self._groq(prompt, model)
                self.last_model = model
                return text
            except Exception as exc:
                errors.append(f"{model}: {_public_error(exc)}")
                self._trip("groq", exc)
        can_gemini = bool(self.gemini_key) and not self.guard.active("gemini")
        can_groq = bool(self.groq_key) and not self.guard.active("groq")
        if not can_gemini and not can_groq and (self.guard.active("gemini") or self.guard.active("groq")):
            raise LimitReached("llm", "AI bị giới hạn")
        if not errors:
            raise LlmError("Thiếu GEMINI_API_KEY hoặc GROQ_API_KEY")
        raise LlmError("; ".join(errors))

    def _trip(self, scope: str, exc: Exception) -> bool:
        if limit_kind(exc) is None:
            return False
        self.guard.trip(scope, "day", f"HTTP giới hạn {scope}")
        return True

    def ping(self) -> str:
        text = self.complete("Reply with the single word ok")
        return text.strip()

    def _gemini(self, prompt: str, model: str) -> str:
        response = self.http.post(
            GEMINI_URL.format(model=model),
            params={"key": self.gemini_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2},
            },
        )
        response.raise_for_status()
        body = response.json()
        return body["candidates"][0]["content"]["parts"][0]["text"]

    def _groq(self, prompt: str, model: str) -> str:
        response = self.http.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self.groq_key}"},
            json={
                "model": model,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        body = response.json()
        return body["choices"][0]["message"]["content"]


def _public_error(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"([?&]key=)[^&\s'\"]+", r"\1***", text)
    text = re.sub(r"Bearer\s+\S+", "Bearer ***", text)
    return text.split("For more information")[0].strip()


def _gemini_models() -> list[str]:
    primary = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    models = [primary]
    for fallback in ("gemini-2.5-flash-lite",):
        if fallback not in models:
            models.append(fallback)
    return models
