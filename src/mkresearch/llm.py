from __future__ import annotations

import os
import re

import httpx

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
    def __init__(self, http: httpx.Client | None = None) -> None:
        self.http = http or httpx.Client(timeout=60)
        self.gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        self.last_model = ""

    def available(self) -> bool:
        return bool(self.gemini_key or self.groq_key)

    def complete(self, prompt: str) -> str:
        errors: list[str] = []
        if self.gemini_key:
            for model in _gemini_models():
                try:
                    text = self._gemini(prompt, model)
                    self.last_model = model
                    return text
                except Exception as exc:
                    errors.append(f"{model}: {exc}")
        if self.groq_key:
            model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
            try:
                text = self._groq(prompt, model)
                self.last_model = model
                return text
            except Exception as exc:
                errors.append(f"{model}: {exc}")
        if not errors:
            raise LlmError("Thiếu GEMINI_API_KEY hoặc GROQ_API_KEY")
        raise LlmError("; ".join(errors))

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


def _gemini_models() -> list[str]:
    primary = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    models = [primary]
    fallback = "gemini-2.0-flash"
    if fallback not in models:
        models.append(fallback)
    return models
