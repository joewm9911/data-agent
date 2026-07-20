"""IdentityProvider / SecretProvider（10.2）：身份与密钥的可插拔接口。

生产实现：OIDC/SAML/LDAP/企业微信/钉钉（Identity），Vault/KMS（Secret）。
本模块提供接口 + 单机实现：静态令牌身份表 + 环境变量/文件密钥。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from da_types import UserIdentity


class IdentityProvider(Protocol):
    async def authenticate(self, token: str) -> UserIdentity | None:
        """令牌 → 身份；无效返回 None。"""
        ...


class SecretProvider(Protocol):
    async def get_secret(self, name: str) -> str | None: ...


class StaticTokenIdentityProvider:
    """静态令牌表（单机/测试）。tokens: {token: UserIdentity}。"""

    def __init__(self, tokens: dict[str, UserIdentity]) -> None:
        self._tokens = dict(tokens)

    async def authenticate(self, token: str) -> UserIdentity | None:
        return self._tokens.get(token)

    @classmethod
    def from_file(cls, path: str | Path) -> StaticTokenIdentityProvider:
        """JSON 文件：{token: {tenant_id, user_id, roles, claims}}。"""
        data = json.loads(Path(path).read_text())
        return cls({t: UserIdentity.model_validate(v) for t, v in data.items()})


class EnvSecretProvider:
    """环境变量密钥（单机）。凭证经此注入，永不落盘快照（D2）。"""

    def __init__(self, prefix: str = "DA_SECRET_") -> None:
        self._prefix = prefix

    async def get_secret(self, name: str) -> str | None:
        return os.environ.get(f"{self._prefix}{name.upper()}") or os.environ.get(name)
