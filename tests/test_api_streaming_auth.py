"""流式 SSE（7.2/D4 解耦）+ Bearer 鉴权（IdentityProvider）+ Web 页面。"""

import json

import pytest
from da_api import AppState, create_app
from da_governance import InMemoryAuditSink
from da_platform.identity import StaticTokenIdentityProvider
from da_runtime import SessionController
from da_semantic import ConfirmationQueue, InMemorySemanticStore
from da_types import UserIdentity
from fastapi.testclient import TestClient
from test_api import FakeAgent


def make_state(identity_provider=None) -> AppState:
    store = InMemorySemanticStore()
    agent = FakeAgent(store)
    state = AppState(
        agent=agent,  # type: ignore[arg-type]
        controller=None,  # type: ignore[arg-type]
        audit=InMemoryAuditSink(),
        confirmations=ConfirmationQueue(store),
        identity_provider=identity_provider,
    )
    state.controller = SessionController(executor=state.make_executor())
    return state


def test_index_page_served():
    client = TestClient(create_app(make_state()))
    r = client.get("/")
    assert r.status_code == 200
    assert "data-agent" in r.text and "EventSource" in r.text


async def test_streaming_turn_via_sse():
    """stream=true 后台执行；SSE 收到 token 流与 done 事件（含最终回答）。

    用 ASGITransport 在同一事件循环内测（同步 TestClient 与长连接流会互相阻塞）。
    """
    import asyncio

    import httpx

    app = create_app(make_state())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        events: list[dict] = []

        async def consume():
            async with client.stream("GET", "/sessions/st1/stream?uid=alice") as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
                        if events[-1]["type"] == "done":
                            return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.1)  # 等订阅建立
        r = await client.post(
            "/sessions/st1/turns",
            json={"question": "GMV?", "stream": True},
            headers={"X-User-Id": "alice", "X-Tenant-Id": "acme"},
        )
        assert r.status_code == 200
        assert r.json()["stream"] == "/sessions/st1/stream"
        await asyncio.wait_for(consumer, timeout=10)

    kinds = [e["type"] for e in events]
    assert "token" in kinds and kinds[-1] == "done"
    assert "第1问的答案" in events[-1]["text"]


@pytest.fixture()
def auth_client():
    provider = StaticTokenIdentityProvider(
        {"tok-alice": UserIdentity(tenant_id="acme", user_id="alice")}
    )
    return TestClient(create_app(make_state(identity_provider=provider)))


def test_bearer_auth_required(auth_client):
    r = auth_client.post("/sessions/s1/turns", json={"question": "q"})
    assert r.status_code == 401

    r = auth_client.post(
        "/sessions/s1/turns", json={"question": "q"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401

    r = auth_client.post(
        "/sessions/s1/turns", json={"question": "q"},
        headers={"Authorization": "Bearer tok-alice"},
    )
    assert r.status_code == 200
    assert "[alice]" in r.json()["answer"]


def test_permission_claims_injected_from_admin(auth_client):
    auth_client.put("/admin/users/alice/permissions",
                    json={"allowed_databases": "main"})
    r = auth_client.post(
        "/sessions/s2/turns", json={"question": "q"},
        headers={"Authorization": "Bearer tok-alice"},
    )
    assert r.status_code == 200  # 权限声明经 apply_permissions 注入 identity
