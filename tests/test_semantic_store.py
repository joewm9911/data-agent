"""语义层版本化存储：每次写入版本 +1，历史完整保留（护城河资产）。"""

from da_semantic import Entity, InMemorySemanticStore


async def test_versioning_and_history():
    store = InMemorySemanticStore()
    entity = Entity(name="客户", canonical_key="customer_id", aliases=["会员"])

    v1 = await store.put("entity", "客户", entity.model_dump(), actor="alice")
    assert v1.version == 1

    entity.aliases.append("买家")
    v2 = await store.put("entity", "客户", entity.model_dump(), actor="bob")
    assert v2.version == 2

    latest = await store.get("entity", "客户")
    assert latest is not None
    assert latest.version == 2
    assert "买家" in latest.payload["aliases"]

    history = await store.history("entity", "客户")
    assert [r.version for r in history] == [1, 2]
    assert history[0].updated_by == "alice"

    assert await store.list_names("entity") == ["客户"]
    assert await store.get("entity", "不存在") is None
