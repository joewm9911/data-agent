"""适配器注册表。新适配器必须通过 conformance 测试套件方可注册（架构文档 11.2）。"""

from __future__ import annotations

from da_connectors.base import Connector

_REGISTRY: dict[str, type[Connector]] = {}


def register_connector(kind: str):
    def decorator(cls: type[Connector]) -> type[Connector]:
        _REGISTRY[kind] = cls
        return cls

    return decorator


def get_connector_cls(kind: str) -> type[Connector]:
    if kind not in _REGISTRY:
        raise KeyError(f"未注册的 connector 类型: {kind}，已注册: {sorted(_REGISTRY)}")
    return _REGISTRY[kind]
