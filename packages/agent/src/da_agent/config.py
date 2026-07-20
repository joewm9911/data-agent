"""LLM 配置。铁律 P5：护城河资产模型无关——模型端点/型号只是配置。

默认走 Anthropic 协议；任何 Anthropic 兼容端点（含 MiniMax 等）经 base_url 接入。
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


class LLMConfig(BaseModel):
    api_key: str
    base_url: str | None = None  # None = Anthropic 官方端点
    model: str = "claude-sonnet-5"
    max_tokens: int = 3000
    timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls) -> LLMConfig:
        api_key = os.environ.get("DA_LLM_API_KEY", "")
        if not api_key:
            raise RuntimeError("DA_LLM_API_KEY 未设置（可放入仓库根 .env）")
        return cls(
            api_key=api_key,
            base_url=os.environ.get("DA_LLM_BASE_URL") or None,
            model=os.environ.get("DA_LLM_MODEL", "claude-sonnet-5"),
        )


def load_dotenv(path: str | Path = ".env") -> None:
    """极简 .env 加载（不引第三方依赖）。已存在的环境变量不覆盖。"""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
