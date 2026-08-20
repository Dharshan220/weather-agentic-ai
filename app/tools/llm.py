from __future__ import annotations

import json
import re
import time
from typing import Dict, List, Optional

import httpx

from ..config import get_settings


class LLMError(Exception):
    pass


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _strip_thinking(text: str) -> str:
    text = re.sub(r"<\|?/?think(ing)?\|?>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^.*?(\{)", r"\1", text, flags=re.DOTALL)
    return text


def _extract_json(text: str) -> Dict:
    text = _strip_code_fences(text)
    text = _strip_thinking(text)
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[m.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            serialized = json.dumps(obj)
            if "|" not in serialized and "..." not in serialized and "<" not in serialized:
                return obj
    start = text.find("{")
    if start == -1:
        raise LLMError(f"LLM returned no JSON object:\n{text[:800]}")
    end = text.rfind("}")
    if end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM returned invalid JSON: {e}\n---\n{text[:800]}") from e
    raise LLMError(f"LLM returned invalid JSON:\n{text[:800]}")


class LLMClient:
    def __init__(self) -> None:
        s = get_settings()
        self.base_url = s.llm_base_url.rstrip("/")
        self.api_key = s.llm_api_key
        self.model = s.llm_model
        self.temperature = s.llm_temperature
        self.timeout = s.llm_timeout_seconds

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, messages: List[Dict], json_mode: bool = False) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
        }
        s = get_settings()
        if s.llm_provider.lower() not in ("google", "gemini"):
            payload["temperature"] = self.temperature
        if json_mode and self.api_key and self.base_url.endswith("/openai/v1") and "groq" not in self.base_url:
            payload["response_format"] = {"type": "json_object"}
        url = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=self.timeout) as client:
            resp = None
            for attempt in range(1, 4):
                resp = client.post(url, headers=self._headers(), json=payload)
                if resp.status_code == 400 and "response_format" in resp.text.lower():
                    payload.pop("response_format", None)
                    resp = client.post(url, headers=self._headers(), json=payload)
                if resp.status_code >= 400 and "temperature" in payload and any(
                    token in resp.text.lower()
                    for token in ("temperature", "top_p", "top_k", "deprecated", "unsupported parameter")
                ):
                    for key in ("temperature", "top_p", "top_k"):
                        payload.pop(key, None)
                    resp = client.post(url, headers=self._headers(), json=payload)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(attempt * 3)
                    continue
                break
            if resp.status_code >= 400:
                raise LLMError(f"LLM API error {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected LLM response shape: {e}") from e

    def chat_json(self, system: str, user: str) -> Dict:
        content = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
        return _extract_json(content)

    def available_models(self) -> List[str]:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/models", headers=self._headers())
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]


def get_llm() -> LLMClient:
    return LLMClient()