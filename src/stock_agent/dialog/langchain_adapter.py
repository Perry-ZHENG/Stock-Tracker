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

    model = ChatOpenAI(model=config.model, api_key=api_key, temperature=0)

    def client(prompt: str) -> str:
        response = model.invoke(prompt)
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
        return str(content)

    return client


__all__ = ["LangChainClient", "build_langchain_client"]
