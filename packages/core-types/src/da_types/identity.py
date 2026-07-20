"""身份与访问契约。铁律 P3：任何查询必须携带真实用户身份执行。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserIdentity(BaseModel):
    """贯穿全链路的用户身份。无身份不执行。"""

    tenant_id: str
    user_id: str
    display_name: str = ""
    roles: list[str] = Field(default_factory=list)
    # 企业侧身份系统的原始声明（SSO claims 等），供权限回调透传
    claims: dict[str, str] = Field(default_factory=dict)


class DataObject(BaseModel):
    """数据对象定位符：库/表/列三级，列可省略表示表级对象。"""

    database: str
    table: str
    column: str | None = None

    def qualified_name(self) -> str:
        base = f"{self.database}.{self.table}"
        return f"{base}.{self.column}" if self.column else base


class ObjectAccess(BaseModel):
    object: DataObject
    allowed: bool
    # 拒绝原因仅用于审计，不回显给终端用户（语义层权限感知：无权对象直接不可见）
    reason: str = ""


class AccessDecision(BaseModel):
    identity_user_id: str
    results: list[ObjectAccess]

    def allowed_objects(self) -> list[DataObject]:
        return [r.object for r in self.results if r.allowed]

    def all_allowed(self) -> bool:
        return all(r.allowed for r in self.results)
