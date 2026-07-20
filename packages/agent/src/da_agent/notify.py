"""IM 通知适配器（9）：晨报/异常简报推到工作流所在地。

Webhook 形态覆盖飞书/钉钉/企微/Slack 的 incoming webhook；传输可注入（测试用 mock）。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

import httpx

IMStyle = Literal["feishu", "dingtalk", "wecom", "slack", "generic"]


class IMNotifier(Protocol):
    async def send(self, title: str, text: str) -> bool: ...


def format_payload(style: IMStyle, title: str, text: str) -> dict[str, Any]:
    if style == "feishu":
        return {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    if style == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
    if style == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n{text}"}}
    if style == "slack":
        return {"text": f"*{title}*\n{text}"}
    return {"title": title, "text": text}


class WebhookNotifier:
    def __init__(
        self,
        url: str,
        style: IMStyle = "generic",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._style = style
        self._client = client

    async def send(self, title: str, text: str) -> bool:
        payload = format_payload(self._style, title, text)
        client = self._client or httpx.AsyncClient(timeout=10)
        owns = self._client is None
        try:
            resp = await client.post(self._url, json=payload)
            return resp.status_code < 300
        except httpx.HTTPError:
            return False
        finally:
            if owns:
                await client.aclose()
