"""Local LLM client for the example apps.

Uses **litellm** with the ``ollama/<model>`` provider — the same integration
pattern as ``openchronicle.writer.llm.call_llm`` (``litellm.completion`` +
``api_base``). This matches how OpenChronicle talks to a local Ollama /
Ollama OpenVINO server regardless of whether the server exposes only native
``/api/chat`` or OpenAI-compatible ``/v1`` routes.

Corporate **HTTP(S) proxy** on loopback is handled like the daemon: we patch
``NO_PROXY`` / ``no_proxy`` for localhost when the model is an ``ollama/...``
route, so requests are not sent through ``HTTP_PROXY`` (which often returns
HTML 403 for ``127.0.0.1:11434``).

Environment (optional; defaults shown):

* ``OC_LLM_BASE_URL`` — ``http://127.0.0.1:11434`` or ``.../v1`` (trailing
  ``/v1`` is stripped for litellm's ``api_base``).
* ``OC_LLM_MODEL`` — bare name (``qwen2.5:7b``) or full litellm id
  (``ollama/qwen2.5:7b``).
* ``OC_LLM_API_KEY`` — defaults to ``ollama``; Ollama ignores it but some
  clients require a non-empty key.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_LOCAL_OLLAMA_NO_PROXY = ("localhost", "127.0.0.1", "::1")

DEFAULT_BASE_URL = os.environ.get("OC_LLM_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("OC_LLM_MODEL", "qwen3_8b_ov:v1")
DEFAULT_API_KEY = os.environ.get("OC_LLM_API_KEY") or "ollama"


def _ensure_local_ollama_proxy_bypass(model: str, api_base: str) -> None:
    """Ensure local Ollama calls are not routed through corporate proxies.

    Mirrors ``openchronicle.writer.llm._ensure_local_ollama_proxy_bypass``.
    """
    if not model.startswith("ollama/"):
        return

    host = urlparse(api_base).hostname if api_base else "localhost"
    if host not in _LOCAL_OLLAMA_NO_PROXY:
        return

    for key in ("NO_PROXY", "no_proxy"):
        existing = os.environ.get(key, "")
        parts = [p.strip() for p in existing.split(",") if p.strip()]
        lower_parts = {p.lower() for p in parts}
        for token in _LOCAL_OLLAMA_NO_PROXY:
            if token.lower() not in lower_parts:
                parts.append(token)
        os.environ[key] = ",".join(parts)


def normalize_litellm_ollama_api_base(base_url: str) -> str:
    """litellm ``api_base`` is the Ollama origin (no ``/v1``)."""
    u = (base_url or "").strip().rstrip("/")
    if not u:
        return "http://127.0.0.1:11434"
    if u.lower().endswith("/v1"):
        u = u[:-3].rstrip("/")
    return u or "http://127.0.0.1:11434"


def litellm_ollama_model(model: str) -> str:
    """Return ``ollama/<name>`` for litellm."""
    m = (model or "").strip()
    if not m:
        m = "qwen3_8b_ov:v1"
    if m.startswith("ollama/"):
        return m
    return f"ollama/{m}"


@dataclass(slots=True)
class LLMConfig:
    """Connection + sampling parameters for the local Ollama model."""

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str = DEFAULT_API_KEY
    temperature: float = 0.2
    max_tokens: int = 4096
    request_timeout: float = 300.0
    trust_env: bool = False


class LLMClient:
    """litellm-backed client for local Ollama / Ollama OpenVINO (OpenChronicle parity)."""

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig()
        normalized = normalize_litellm_ollama_api_base(self.config.base_url)
        if normalized != self.config.base_url.strip().rstrip("/"):
            logger.info(
                "Normalized LLM base_url %r -> %r (litellm ollama api_base has no /v1 suffix)",
                self.config.base_url,
                normalized,
            )
        self.config.base_url = normalized

    def health_check(self) -> bool:
        """GET ``/api/version`` on the configured Ollama origin."""
        url = self.config.base_url.rstrip("/") + "/api/version"
        try:
            with httpx.Client(timeout=5.0, trust_env=self.config.trust_env) as client:
                resp = client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Ollama health check failed at %s: %s", url, exc)
            return False
        return True

    def _parse_chat_content(self, raw: str, json_mode: bool) -> Any:
        if not json_mode:
            return raw
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if "\n" in cleaned:
                cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning(
                "LLM JSON parse failed (%s); model=%s. Returning raw text under '_raw'.",
                exc,
                self.config.model,
            )
            return {"_raw": raw, "_error": str(exc)}

    def chat(
        self,
        system: str,
        user: str,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> Any:
        import litellm  # lazy: keeps CLI import light when LLM not used

        model = litellm_ollama_model(self.config.model)
        _ensure_local_ollama_proxy_bypass(model, self.config.base_url)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "api_base": self.config.base_url,
            "temperature": self.config.temperature,
            "timeout": self.config.request_timeout,
        }
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        mt = max_tokens if max_tokens is not None else self.config.max_tokens
        if mt:
            kwargs["max_tokens"] = mt
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"LLM request failed (litellm, model={model!r}, "
                f"api_base={self.config.base_url!r}): {exc}. "
                "Confirm Ollama is running, the model is pulled, and "
                "corporate proxy is not intercepting loopback (see NO_PROXY for localhost)."
            ) from exc

        try:
            content = response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response shape: {response!r}") from exc

        return self._parse_chat_content(content, json_mode)
