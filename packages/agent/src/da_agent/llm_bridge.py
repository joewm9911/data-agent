"""LLM 桥：把 LLMClient 适配成语义层/eval 的 CompleteFn（Callable[[str], Awaitable[str]]）。

语义层与 eval 不依赖具体模型客户端（铁律 P5），本桥是唯一的粘合点。
"""

from __future__ import annotations

from da_agent.llm import LLMClient


def as_complete_fn(llm: LLMClient):
    async def complete(prompt: str) -> str:
        message = await llm.create(
            system="你是数据语义专家，严格按要求的格式输出，不要多余解释。",
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in message.content if b.type == "text")

    return complete
