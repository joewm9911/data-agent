"""治理平面：护栏与审计。横切所有层，适配器必须经由本包执行护栏。"""

from da_governance.audit import AuditEvent, AuditSink, InMemoryAuditSink, JsonlAuditSink
from da_governance.guard import (
    prepare_statement,
    referenced_objects,
    sanitize_result_text,
)

__all__ = [
    "AuditEvent",
    "AuditSink",
    "InMemoryAuditSink",
    "JsonlAuditSink",
    "prepare_statement",
    "referenced_objects",
    "sanitize_result_text",
]
