"""LLM 客户端：Anthropic 协议薄封装，支持流式（token 经回调外发，7.2 流式解耦）。

附带 token 用量累计（8.2 成本统计）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anthropic

from da_agent.config import LLMConfig

OnToken = Callable[[str], Awaitable[None]]


@dataclass
class UsageCounter:
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0

    def add(self, usage: Any) -> None:
        self.llm_calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0


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
        on_token: OnToken | None = None,
        usage: UsageCounter | None = None,
    ) -> anthropic.types.Message:
        if on_token is None:
            message = await self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=system,
                messages=messages,
                tools=tools or [],
            )
        else:
            async with self._client.messages.stream(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=system,
                messages=messages,
                tools=tools or [],
            ) as stream:
                async for text in stream.text_stream:
                    await on_token(text)
                message = await stream.get_final_message()
        if usage is not None:
            usage.add(message.usage)
        return message
