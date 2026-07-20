"""LLM 客户端：Anthropic 协议薄封装。"""

from __future__ import annotations

from typing import Any

import anthropic

from da_agent.config import LLMConfig


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = anthropic.AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    async def create(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> anthropic.types.Message:
        return await self._client.messages.create(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            system=system,
            messages=messages,
            tools=tools or [],
        )
