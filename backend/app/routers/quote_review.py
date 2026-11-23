"""报价差异复核接口：按字段查差异、开复核单、按单取样、当前复验和质检台账。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult, PageResult, QuoteBatchPayload, ReviewTicketPayload, ReverifyPayload,
)
from app.services import quote_review
from app.store import store

router = APIRouter(prefix="/api/qc", tags=["报价差异复核"])


def _bad_request(error: quote_review.QuoteReviewError) -> HTTPException:
    status = 409 if isinstance(error, quote_review.QuoteVersionConflict) else 400
    return HTTPException(status_code=status, detail=str(error))


@router.get("/batches")
def list_batches() -> dict[str, Any]:
    return {"items": quote_review.list_batches()}


@router.post("/batches")
def publish_batch(payload: QuoteBatchPayload) -> ActionResult:
    try:
        result = quote_review.publish_batch(
            payload.version,
            payload.rows,
            parent_version=payload.parent_version,
            operator=payload.operator,
        )
    except quote_review.QuoteReviewError as error:
        raise _bad_request(error) from error
    batch = result["batch"]
    return ActionResult(
        ok=True,
        message=(
            f"报价批次 {batch['批次版本']} 已发布，"
            f"{batch['报价数量']} 条报价、{batch['字段差异数']} 处字段差异"
        ),
        entry=batch,
    )


@router.get("/quotes")
def list_quotes(version: str) -> dict[str, Any]:
    if version not in {str(row.get("批次版本")) for row in store.rows("quote_batch")}:
        raise HTTPException(status_code=404, detail=f"报价批次版本 {version} 不存在")
    return {"version": version, "items": list(quote_review._lines(version).values())}


@router.get("/diffs", response_model=PageResult[dict])
def query_diffs(
    field: str = Query(..., min_length=1, description="要查询的差异字段，如 金额、标题"),
    version: str | None = Query(default=None),
    quote_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    try:
        items, total = quote_review.query_field_diffs(
            field=field, version=version, quote_id=quote_id, page=page, size=size,
        )
    except quote_review.QuoteReviewError as error:
        raise _bad_request(error) from error
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/review-tickets")
def create_tickets(payload: ReviewTicketPayload) -> dict[str, Any]:
    try:
        return quote_review.create_review_tickets(
            payload.diff_ids,
            field=payload.field,
            version=payload.version,
            quote_id=payload.quote_id,
            operator=payload.operator,
            idempotency_key=payload.idempotency_key,
        )
    except quote_review.QuoteReviewError as error:
        raise _bad_request(error) from error


@router.get("/review-tickets", response_model=PageResult[dict])
def list_tickets(
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    items, total = quote_review.list_review_tickets(status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/review-tickets/{ticket_id}")
def get_ticket(ticket_id: str) -> dict[str, Any]:
    detail = quote_review.get_review_ticket(ticket_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"复核单 {ticket_id} 不存在")
    return detail


@router.post("/review-tickets/{ticket_id}/sample")
def sample_ticket(ticket_id: str, operator: str = Query(default="值班员")) -> dict[str, Any]:
    try:
        return quote_review.sample_ticket(ticket_id, operator=operator)
    except quote_review.QuoteVersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except quote_review.QuoteReviewError as error:
        raise _bad_request(error) from error


@router.post("/reverify")
def reverify(payload: ReverifyPayload) -> dict[str, Any]:
    try:
        return quote_review.reverify_current(
            field=payload.field,
            baseline_version=payload.baseline_version,
            current_version=payload.current_version,
            quota=payload.quota,
            operator=payload.operator,
        )
    except quote_review.QuoteReviewError as error:
        raise _bad_request(error) from error


@router.get("/quotes/{quote_id}/history")
def get_quote_history(
    quote_id: str,
    field: str | None = Query(default=None, description="只看某字段，如 金额"),
) -> dict[str, Any]:
    try:
        return quote_review.quote_history(quote_id, field=field)
    except quote_review.QuoteReviewError as error:
        raise _bad_request(error) from error


@router.get("/ledger", response_model=PageResult[dict])
def list_ledger(
    field: str | None = Query(default=None),
    quote_id: str | None = Query(default=None),
    ticket_id: str | None = Query(default=None),
    sample_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    items, total = quote_review.list_ledger(
        field=field, quote_id=quote_id, ticket_id=ticket_id,
        sample_type=sample_type, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/demo/bootstrap")
def bootstrap_demo() -> dict[str, Any]:
    return quote_review.bootstrap_demo()


@router.post("/demo/correction")
def publish_demo_correction() -> dict[str, Any]:
    try:
        return quote_review.publish_demo_correction()
    except quote_review.QuoteReviewError as error:
        raise _bad_request(error) from error
