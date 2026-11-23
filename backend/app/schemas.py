"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


# ---------------------------------------------------------------------------
# 报价差异复核专用契约
# ---------------------------------------------------------------------------

class QuoteBatchPayload(BaseModel):
    version: str
    parent_version: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    operator: str = "值班员"


class ReviewTicketPayload(BaseModel):
    diff_ids: list[int] = Field(default_factory=list)
    field: str | None = None
    version: str | None = None
    quote_id: str | None = None
    operator: str = "值班员"
    idempotency_key: str | None = None


class ReverifyPayload(BaseModel):
    field: str = "金额"
    baseline_version: str = "V0"
    current_version: str | None = None
    quota: int = 2
    operator: str = "值班员"
