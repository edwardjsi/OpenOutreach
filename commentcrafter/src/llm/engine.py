"""LLM model factory — adapted from OpenOutreach's proven pattern.

Provides a persistent asyncio event loop on a daemon thread so
pydantic-ai agents work correctly alongside sync Playwright code.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Awaitable, TypeVar

from termcolor import colored

from src.config import LLM_DEFAULT_PROVIDER, LLM_DEFAULT_MODEL, LLM_MAX_RETRIES, LLM_TIMEOUT_S

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


# ── Async runner ────────────────────────────────────────────────

class _Runner:
    """Owns one persistent asyncio loop on a dedicated daemon thread."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        ready = threading.Event()
        threading.Thread(
            target=self._serve, args=(ready,), daemon=True, name="llm-runner",
        ).start()
        ready.wait()

    def _serve(self, ready: threading.Event) -> None:
        asyncio.set_event_loop(self._loop)
        ready.set()
        self._loop.run_forever()

    def run(self, coro: Awaitable[_T]) -> _T:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


_runner: _Runner | None = None
_runner_lock = threading.Lock()


def _get_runner() -> _Runner:
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                _runner = _Runner()
    return _runner


def run_agent_sync(coro: Awaitable[_T]) -> _T:
    """Drive a pydantic-ai coroutine on the dedicated LLM thread."""
    return _get_runner().run(coro)


# ── Model factory ──────────────────────────────────────────────

_PROVIDER_BUILDERS: dict[str, callable] = {}


def _ensure_builders():
    """Lazy-import provider builders to keep imports fast."""
    if _PROVIDER_BUILDERS:
        return

    def _build_openai(cfg):
        from openai import AsyncOpenAI
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        client = AsyncOpenAI(api_key=cfg.get("api_key"), max_retries=cfg.get("max_retries", LLM_MAX_RETRIES))
        return OpenAIChatModel(cfg.get("model", LLM_DEFAULT_MODEL), provider=OpenAIProvider(openai_client=client))

    def _build_anthropic(cfg):
        from anthropic import AsyncAnthropic
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        client = AsyncAnthropic(api_key=cfg.get("api_key"), max_retries=cfg.get("max_retries", LLM_MAX_RETRIES))
        return AnthropicModel(cfg.get("model"), provider=AnthropicProvider(anthropic_client=client))

    def _build_google(cfg):
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider
        return GoogleModel(cfg.get("model"), provider=GoogleProvider(api_key=cfg.get("api_key")))

    def _build_groq(cfg):
        from groq import AsyncGroq
        from pydantic_ai.models.groq import GroqModel
        from pydantic_ai.providers.groq import GroqProvider
        client = AsyncGroq(api_key=cfg.get("api_key"), max_retries=cfg.get("max_retries", LLM_MAX_RETRIES))
        return GroqModel(cfg.get("model"), provider=GroqProvider(groq_client=client))

    def _build_mistral(cfg):
        from pydantic_ai.models.mistral import MistralModel
        from pydantic_ai.providers.mistral import MistralProvider
        return MistralModel(cfg.get("model"), provider=MistralProvider(api_key=cfg.get("api_key")))

    def _build_cohere(cfg):
        from pydantic_ai.models.cohere import CohereModel
        from pydantic_ai.providers.cohere import CohereProvider
        return CohereModel(cfg.get("model"), provider=CohereProvider(api_key=cfg.get("api_key")))

    def _build_openai_compatible(cfg):
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        return OpenAIChatModel(
            cfg.get("model"),
            provider=OpenAIProvider(base_url=cfg.get("api_base"), api_key=cfg.get("api_key")),
        )

    _PROVIDER_BUILDERS.update({
        "openai": _build_openai,
        "anthropic": _build_anthropic,
        "google": _build_google,
        "groq": _build_groq,
        "mistral": _build_mistral,
        "cohere": _build_cohere,
        "openai_compatible": _build_openai_compatible,
    })


def get_llm_model():
    """Return a configured pydantic-ai Model from stored config."""
    from src.models import get_config

    _ensure_builders()

    provider = get_config("llm_provider", LLM_DEFAULT_PROVIDER)
    api_key = get_config("llm_api_key", "")
    model = get_config("ai_model", LLM_DEFAULT_MODEL)
    api_base = get_config("llm_api_base", "")

    cfg = {"api_key": api_key, "model": model, "max_retries": LLM_MAX_RETRIES}
    if api_base:
        cfg["api_base"] = api_base

    builder = _PROVIDER_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Supported: {list(_PROVIDER_BUILDERS)}")

    if not api_key:
        raise ValueError("LLM_API_KEY is not set. Run `python -m src.main config` to configure.")

    logger.info("LLM: provider=%s model=%s", provider, model)
    return builder(cfg)
