"""Optional LangChain client adapter for dialog parsing and chat."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from stock_agent.config import LlmConfig

LangChainClient = Callable[[str], str]


def build_langchain_client(
    config: LlmConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> LangChainClient | None:
    """Build a LangChain OpenAI chat client when optional dependencies exist.

    The project keeps LangChain optional so offline demo and tests can run
    without network access, API keys, or extra packages.
    """

    if not config.enabled:
        return None
    env = environ or os.environ
    api_key = env.get(config.api_key_env)
    if not api_key:
        return None
    try:
        from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
    except ImportError:
        return None

    def create_model(model_name: str):
        model_kwargs = {
            "model": model_name,
            "api_key": api_key,
            "temperature": 0,
            "timeout": config.request_timeout_sec,
            "max_retries": config.max_retries,
        }
        if config.base_url:
            model_kwargs["base_url"] = config.base_url
        return ChatOpenAI(**model_kwargs)

    model = create_model(config.model)
    fallback_model = (
        create_model(config.fallback_model)
        if config.fallback_model and config.fallback_model != config.model
        else None
    )

    def client(prompt: str) -> str:
        try:
            response = model.invoke(prompt)
        except Exception as exc:
            if fallback_model is None or not _is_retryable_provider_error(exc):
                raise
            response = fallback_model.invoke(prompt)
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
        return str(content)

    return client


def _is_retryable_provider_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {429, 502, 503, 504}:
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("429", "rate limit", "temporarily unavailable", "provider returned error")
    )


__all__ = ["LangChainClient", "build_langchain_client"]
