import json
import logging
import os
import re
from typing import Protocol

from config import Settings

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMClient(Protocol):
    name: str
    is_live: bool

    def complete(self, system: str, prompt: str, max_tokens: int = 1024) -> str: ...


class MockLLM:
    # no model calls. every agent has a deterministic fallback for an empty reply
    name = "mock"
    is_live = False

    def complete(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        return ""


class AnthropicLLM:
    is_live = True

    def __init__(self, api_key: str, model: str, timeout_s: float):
        import anthropic

        self.name = model
        self._model = model
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)

    def complete(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in resp.content if block.type == "text")
        except Exception as exc:
            # callers treat an empty string as "fall back to the deterministic path"
            logger.warning(f"llm call failed: {exc}")
            return ""


def complete_json(llm: LLMClient, system: str, prompt: str, max_tokens: int = 800) -> dict | None:
    raw = llm.complete(system, prompt, max_tokens)
    if not raw.strip():
        return None
    try:
        parsed = json.loads(_FENCE.sub("", raw.strip()))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        logger.warning(f"llm returned non-json output: {raw[:120]!r}")
        return None


def build_llm(settings: Settings) -> LLMClient:
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if settings.llm_provider == "anthropic" and key:
        return AnthropicLLM(key, settings.llm_model, settings.llm_timeout_s)
    if settings.llm_provider == "anthropic":
        logger.warning("llm_provider is anthropic but no api key found, using mock")
    return MockLLM()
