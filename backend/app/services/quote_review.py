"""报价差异复核领域服务：字段级差异 → 复核单 → 按单取样 → 质检台账。

关键约束：
- 差异必须带字段名、前值、后值和来源版本，可按字段检索并分页。
- 复核单在创建时快照字段、前后值和来源版本；后续更正批只更新实时值，
  不会改写原单快照，也不会把已确认样本悄悄换成另一版。
- 同一处差异重复提交只保留一份有效复核单（同时支持提交幂等键）。
- 质量抽样按复核单快照读取来源版本，样本结果回写质检台账并标明发布版本。
- 当前复验按“当前版本 vs 基线版本”重新判断差异；更正回基线的报价不再计为异常，
  其复核单名额释放给仍有差异的报价，未入样必须写明更正依据。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.store import store

QUOTE_ID_KEYS = ("报价编号", "quote_id", "id")
QUOTE_META_KEYS = {
    "id", "quote_id", "报价编号", "批次版本", "status", "pending", "abnormal",
}
DIFF_FIELDS = (
    "id", "记录编号", "报价编号", "字段名", "前值", "后值",
    "基线版本", "来源版本", "变更类型", "检测时间",
)
TICKET_FIELDS = (
    "复核单编号", "报价编号", "字段名", "前值", "后值", "基线版本", "来源版本",
    "状态", "锁定", "样本编号", "更正依据", "创建人", "创建时间",
)
LEDGER_FIELDS = (
    "台账编号", "复核单编号", "报价编号", "字段名", "样本值", "发布版本",
    "基线值", "基线版本", "样本类型", "入样", "结果", "理由", "取样人", "取样时间",
)


class QuoteReviewError(Exception):
    """报价复核业务错误，路由层转换为 4xx。"""


class QuoteVersionConflict(QuoteReviewError):
    """来源版本的实际值与复核单快照不一致，拒绝静默换版取样。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(module: str) -> int:
    rows = store.rows(module)
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _next_code(module: str, prefix: str, code_field: str) -> str:
    used = {str(row.get(code_field) or "") for row in store.rows(module)}
    index = 1
    while f"{prefix}-{index:04d}" in used:
        index += 1
    return f"{prefix}-{index:04d}"


def _normalize(field: str, value: Any) -> Any:
    """把金额统一成数值，其余字段按文本比较，避免 8 与 "8" 被误判为差异。"""
    if value is None:
        return None
    if field == "金额":
        if isinstance(value, bool):
            return value
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value).strip()
        return int(number) if number.is_integer() else number
    return str(value).strip()


def _same(field: str, left: Any, right: Any) -> bool:
    return _normalize(field, left) == _normalize(field, right)


def _quote_id(row: dict[str, Any]) -> str:
    for key in QUOTE_ID_KEYS:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise QuoteReviewError("报价行缺少报价编号")


def _row_values(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _normalize(key, value)
        for key, value in row.items()
        if key not in QUOTE_META_KEYS and key != "status" and value is not None
    }


def _published_versions() -> list[dict[str, Any]]:
    return sorted(
        (row for row in store.rows("quote_batch") if row.get("状态") == "已发布"),
        key=lambda row: int(row.get("id", 0)),
    )


def _latest_version() -> str:
    versions = _published_versions()
    if not versions:
        raise QuoteReviewError("尚未发布任何报价批次")
    return str(versions[-1]["批次版本"])


def _version_row(version: str) -> dict[str, Any] | None:
    for row in store.rows("quote_batch"):
        if str(row.get("批次版本")) == version:
            return row
    return None


def _require_version(version: str) -> dict[str, Any]:
    row = _version_row(version)
    if row is None:
        raise QuoteReviewError(f"报价批次版本 {version} 不存在")
    return row


def _lines(version: str) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("报价编号")): row
        for row in store.rows("quote_line")
        if str(row.get("批次版本")) == version
    }


def _value_of(version: str, quote_id: str, field: str) -> tuple[bool, Any]:
    row = _lines(version).get(str(quote_id))
    if row is None:
        return False, None
    return True, row.get(field)


# ---------------------------------------------------------------------------
# 批次发布与字段差异
# ---------------------------------------------------------------------------

def publish_batch(
    version: str,
    rows: Iterable[dict[str, Any]],
    *,
    parent_version: str | None = None,
    operator: str = "值班员",
) -> dict[str, Any]:
    """发布一个报价批次版本，并相对父版本生成字段级差异。"""
    version = str(version).strip()
    if not version:
        raise QuoteReviewError("批次版本不能为空")
    if _version_row(version) is not None:
        raise QuoteReviewError(f"报价批次版本 {version} 已存在，不能重复发布")

    published = _published_versions()
    if parent_version is None:
        parent_version = str(published[-1]["批次版本"]) if published else None
    if parent_version is not None:
        _require_version(parent_version)

    # 同一版本内报价编号必须唯一
    current: dict[str, dict[str, Any]] = {}
    for raw in rows:
        quote_id = _quote_id(raw)
        if quote_id in current:
            raise QuoteReviewError(f"批次 {version} 中报价 {quote_id} 重复")
        current[quote_id] = _row_values(raw)
    if not current:
        raise QuoteReviewError("报价批次不能为空")

    previous: dict[str, dict[str, Any]] = {}
    if parent_version is not None:
        previous = {
            quote_id: _row_values(row) for quote_id, row in _lines(parent_version).items()
        }

    batch_id = _next_id("quote_batch")
    diffs: list[dict[str, Any]] = []
    if parent_version is not None:
        for quote_id in sorted(set(previous) | set(current)):
            before_row = previous.get(quote_id)
            after_row = current.get(quote_id)
            if before_row is None:
                change_type = "新增报价"
                fields = sorted(after_row or {})
            elif after_row is None:
                change_type = "删除报价"
                fields = sorted(before_row)
            else:
                change_type = "字段变更"
                fields = sorted(set(before_row) | set(after_row))
            for field in fields:
                before = before_row.get(field) if before_row else None
                after = after_row.get(field) if after_row else None
                if before_row is not None and after_row is not None and _same(field, before, after):
                    continue
                diff_id = _next_id("quote_diff")
                diff = {
                    "id": diff_id,
                    "记录编号": _next_code("quote_diff", "QDIFF", "记录编号"),
                    "报价编号": quote_id,
                    "字段名": field,
                    "前值": before,
                    "后值": after,
                    "基线版本": parent_version,
                    "来源版本": version,
                    "变更类型": change_type,
                    "检测时间": _now(),
                    "复核单编号": None,
                    "已取样": False,
                    "status": "已检测",
                    "pending": True,
                    "abnormal": False,
                }
                store.rows("quote_diff").append(diff)
                diffs.append(dict(diff))

    batch = {
        "id": batch_id,
        "批次版本": version,
        "父版本": parent_version or "",
        "报价数量": len(current),
        "字段差异数": len(diffs),
        "状态": "已发布",
        "发布时间": _now(),
        "操作人": operator,
        "status": "已发布",
        "pending": False,
        "abnormal": False,
    }
    store.rows("quote_batch").append(batch)

    line_id = _next_id("quote_line")
    for quote_id in sorted(current):
        store.rows("quote_line").append({
            "id": line_id,
            "批次版本": version,
            "报价编号": quote_id,
            **current[quote_id],
        })
        line_id += 1

    return {"batch": dict(batch), "diffs": diffs}


def list_batches() -> list[dict[str, Any]]:
    return [dict(row) for row in _published_versions()]


def query_field_diffs(
    *,
    field: str,
    version: str | None = None,
    quote_id: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """按字段读取差异，返回带前值/后值/来源版本的分页结果。"""
    rows = _field_diff_rows(field=field, version=version, quote_id=quote_id)
    total = len(rows)
    page = max(1, int(page))
    size = max(1, min(int(size), 200))
    start = (page - 1) * size
    items = [{key: row.get(key) for key in DIFF_FIELDS} for row in rows[start:start + size]]
    return items, total


def _field_diff_rows(
    *, field: str, version: str | None = None, quote_id: str | None = None,
) -> list[dict[str, Any]]:
    field = str(field or "").strip()
    if not field:
        raise QuoteReviewError("必须指定要查询的差异字段")
    rows = [
        row for row in store.rows("quote_diff")
        if str(row.get("字段名")) == field
    ]
    if version:
        rows = [row for row in rows if str(row.get("来源版本")) == str(version)]
    if quote_id:
        rows = [row for row in rows if str(row.get("报价编号")) == str(quote_id)]
    rows.sort(key=lambda row: int(row.get("id", 0)))
    return rows


# ---------------------------------------------------------------------------
# 字段差异复核单
# ---------------------------------------------------------------------------

def _ticket_key(quote_id: str, field: str, baseline_version: str, source_version: str) -> str:
    return f"{quote_id}|{field}|{baseline_version}|{source_version}"


def _active_tickets() -> list[dict[str, Any]]:
    return [
        row for row in store.rows("review_ticket")
        if row.get("状态") not in ("已作废",)
    ]


def _ticket_by_code(ticket_id: str) -> dict[str, Any] | None:
    for row in store.rows("review_ticket"):
        if str(row.get("复核单编号")) == str(ticket_id):
            return row
    return None


def create_review_tickets(
    diff_ids: Iterable[int] | None = None,
    *,
    field: str | None = None,
    version: str | None = None,
    quote_id: str | None = None,
    operator: str = "值班员",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """从字段差异检索结果开复核单；同一处差异重复提交只保留一份有效单据。"""
    selected: list[dict[str, Any]] = []
    if diff_ids:
        wanted = [int(item) for item in diff_ids]
        by_id = {int(row.get("id", 0)): row for row in store.rows("quote_diff")}
        missing = [item for item in wanted if item not in by_id]
        if missing:
            raise QuoteReviewError(f"差异记录不存在：{missing}")
        selected = [by_id[item] for item in wanted]
    elif field:
        selected = _field_diff_rows(field=field, version=version, quote_id=quote_id)
    if not selected:
        raise QuoteReviewError("没有可开复核单的字段差异")

    created: list[dict[str, Any]] = []
    reused: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    if idempotency_key:
        previous = [
            row for row in _active_tickets()
            if str(row.get("提交幂等键") or "") == str(idempotency_key)
        ]
        if previous:
            return {
                "ok": True,
                "duplicate": True,
                "created": [],
                "reused": [dict(row) for row in previous],
                "message": "同一提交幂等键已登记过复核单，返回原单",
            }

    for diff in selected:
        key = _ticket_key(
            str(diff.get("报价编号")),
            str(diff.get("字段名")),
            str(diff.get("基线版本")),
            str(diff.get("来源版本")),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        existing = next(
            (row for row in _active_tickets() if str(row.get("单据键") or "") == key),
            None,
        )
        if existing is not None:
            reused.append(dict(existing))
            continue

        ticket = {
            "id": _next_id("review_ticket"),
            "复核单编号": _next_code("review_ticket", "RVT", "复核单编号"),
            "报价编号": str(diff.get("报价编号")),
            "字段名": str(diff.get("字段名")),
            "前值": diff.get("前值"),
            "后值": diff.get("后值"),
            "基线版本": str(diff.get("基线版本")),
            "来源版本": str(diff.get("来源版本")),
            "状态": "待取样",
            "锁定": False,
            "样本编号": None,
            "创建人": operator,
            "创建时间": _now(),
            "确认时间": None,
            "来源差异编号": diff.get("记录编号"),
            "单据键": key,
            "提交幂等键": idempotency_key or "",
            "更正依据": "",
            "status": "待取样",
            "pending": True,
            "abnormal": False,
        }
        store.rows("review_ticket").append(ticket)
        diff["复核单编号"] = ticket["复核单编号"]
        diff["已取样"] = False
        created.append(dict(ticket))

    duplicate = bool(reused)
    message = "复核单已登记" if created else "同一处差异已有有效复核单，未重复登记"
    if created and reused:
        message = "新复核单已登记，重复差异沿用原单"
    return {
        "ok": True,
        "duplicate": duplicate,
        "created": created,
        "reused": reused,
        "message": message,
    }


def list_review_tickets(
    *, status: str | None = None, page: int = 1, size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    rows = [row for row in store.rows("review_ticket") if status is None or row.get("状态") == status]
    rows.sort(key=lambda row: int(row.get("id", 0)))
    total = len(rows)
    page = max(1, int(page))
    size = max(1, min(int(size), 200))
    start = (page - 1) * size
    return [{key: row.get(key) for key in TICKET_FIELDS} for row in rows[start:start + size]], total


def get_review_ticket(ticket_id: str) -> dict[str, Any] | None:
    """回查原单快照，并给出当前批次实时值，二者分开呈现。"""
    ticket = _ticket_by_code(ticket_id)
    if ticket is None:
        return None
    snapshot = {
        "字段名": ticket.get("字段名"),
        "前值": ticket.get("前值"),
        "后值": ticket.get("后值"),
        "基线版本": ticket.get("基线版本"),
        "来源版本": ticket.get("来源版本"),
    }
    latest = str(_published_versions()[-1]["批次版本"]) if _published_versions() else None
    live: dict[str, Any] = {"当前版本": latest, "当前值": None, "存在": False}
    if latest:
        exists, value = _value_of(latest, str(ticket.get("报价编号")), str(ticket.get("字段名")))
        live = {"当前版本": latest, "当前值": value, "存在": exists}
    version_changed = latest is not None and latest != ticket.get("来源版本")
    note = ""
    if version_changed:
        same_as_baseline = _same(
            str(ticket.get("字段名")),
            live.get("当前值"),
            ticket.get("前值"),
        )
        if same_as_baseline:
            note = (
                f"来源版本 {ticket.get('来源版本')} 的发布值为 {ticket.get('后值')}；"
                f"当前 {latest} 已更正为 {live.get('当前值')}，与基线 "
                f"{ticket.get('基线版本')} 的 {ticket.get('前值')} 一致。原单快照保持不变。"
            )
        else:
            note = (
                f"来源版本 {ticket.get('来源版本')} 的发布值为 {ticket.get('后值')}；"
                f"当前 {latest} 的值为 {live.get('当前值')}。原单快照保持不变。"
            )
    return {
        "ticket": dict(ticket),
        "snapshot": snapshot,
        "live": live,
        "version_changed": version_changed,
        "note": note,
    }


# ---------------------------------------------------------------------------
# 按原单取样
# ---------------------------------------------------------------------------

def _ledger_entry(
    *,
    ticket_id: str | None,
    quote_id: str,
    field: str,
    sample_value: Any,
    published_version: str,
    baseline_value: Any,
    baseline_version: str,
    sample_type: str,
    included: bool,
    result: str,
    reason: str,
    operator: str,
) -> dict[str, Any]:
    entry = {
        "id": _next_id("quality_ledger"),
        "台账编号": _next_code("quality_ledger", "QL", "台账编号"),
        "复核单编号": ticket_id or "",
        "报价编号": quote_id,
        "字段名": field,
        "样本值": sample_value,
        "发布版本": published_version,
        "基线值": baseline_value,
        "基线版本": baseline_version,
        "样本类型": sample_type,
        "入样": included,
        "结果": result,
        "理由": reason,
        "取样人": operator,
        "取样时间": _now(),
        "status": "已取样" if included else "未入样",
        "pending": False,
        "abnormal": bool(included and result == "差异成立"),
    }
    store.rows("quality_ledger").append(entry)
    return dict(entry)


def sample_ticket(ticket_id: str, *, operator: str = "值班员") -> dict[str, Any]:
    """按复核单快照从来源版本取样，结果回写质检台账；已确认样本锁定不换版。"""
    ticket = _ticket_by_code(ticket_id)
    if ticket is None:
        raise QuoteReviewError(f"复核单 {ticket_id} 不存在")
    if ticket.get("状态") == "已作废":
        raise QuoteReviewError(f"复核单 {ticket_id} 已作废，不能取样")

    if ticket.get("状态") == "已取样" or ticket.get("锁定"):
        existing = next(
            (
                row for row in store.rows("quality_ledger")
                if row.get("复核单编号") == ticket_id and row.get("样本类型") == "复核单取样"
            ),
            None,
        )
        return {
            "ok": True,
            "duplicate": True,
            "ticket": dict(ticket),
            "sample": dict(existing) if existing else None,
            "message": "该复核单已确认取样，返回原锁定样本，不替换为其他版本",
        }

    source_version = str(ticket.get("来源版本"))
    field = str(ticket.get("字段名"))
    quote_id = str(ticket.get("报价编号"))
    exists, actual = _value_of(source_version, quote_id, field)
    if not exists:
        raise QuoteVersionConflict(
            f"来源版本 {source_version} 中找不到报价 {quote_id}，拒绝取样"
        )
    if not _same(field, actual, ticket.get("后值")):
        ticket["abnormal"] = True
        raise QuoteVersionConflict(
            f"来源版本 {source_version} 的实际值为 {actual}，与复核单快照 {ticket.get('后值')} 不一致，"
            "已阻止按另一版静默取样"
        )

    sample = _ledger_entry(
        ticket_id=ticket_id,
        quote_id=quote_id,
        field=field,
        sample_value=ticket.get("后值"),
        published_version=source_version,
        baseline_value=ticket.get("前值"),
        baseline_version=str(ticket.get("基线版本")),
        sample_type="复核单取样",
        included=True,
        result="差异成立",
        reason=(
            f"按原复核单 {ticket_id} 取样，锁定 {source_version} 发布值 "
            f"{ticket.get('后值')}（基线 {ticket.get('基线版本')} 为 {ticket.get('前值')}）"
        ),
        operator=operator,
    )
    ticket["状态"] = "已取样"
    ticket["status"] = "已取样"
    ticket["锁定"] = True
    ticket["样本编号"] = sample["台账编号"]
    ticket["确认时间"] = sample["取样时间"]
    ticket["pending"] = False
    ticket["abnormal"] = True

    for diff in store.rows("quote_diff"):
        if diff.get("复核单编号") == ticket_id:
            diff["已取样"] = True
            diff["status"] = "已取样"

    return {
        "ok": True,
        "duplicate": False,
        "ticket": dict(ticket),
        "sample": sample,
        "message": f"复核单 {ticket_id} 已按 {source_version} 原发布值取样并写入质检台账",
    }


# ---------------------------------------------------------------------------
# 当前复验
# ---------------------------------------------------------------------------

def _record_quality_plan(
    *, current_version: str, field: str, sampled: int, candidates: int,
    not_sampled: int, abnormal: bool, operator: str,
) -> dict[str, Any]:
    rows = store.rows("quality_sample")
    plan = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "计划编号": "",
        "目标批次": current_version,
        "抽样比例": "",
        "抽样方式": "字段差异复核",
        "样本数量": sampled,
        "检验结果": (
            f"{field}当前复验：候选{candidates}条，入样{sampled}条，未入样{not_sampled}条"
        ),
        "偏差阈值": "",
        "最近采样": _now(),
        "操作人": operator,
        "status": "偏差告警" if abnormal else "已通过",
        "pending": False,
        "abnormal": abnormal,
    }
    plan["计划编号"] = f"QS-{plan['id']:04d}"
    rows.append(plan)
    return dict(plan)


def reverify_current(
    *,
    field: str = "金额",
    baseline_version: str,
    current_version: str | None = None,
    quota: int = 2,
    operator: str = "值班员",
) -> dict[str, Any]:
    """当前复验：按当前版本对基线重新判差异，名额优先给仍有差异的报价。"""
    field = str(field or "").strip()
    if not field:
        raise QuoteReviewError("必须指定复验字段")
    baseline_version = str(baseline_version)
    _require_version(baseline_version)
    if current_version is None:
        current_version = _latest_version()
    current_version = str(current_version)
    _require_version(current_version)
    if current_version == baseline_version:
        raise QuoteReviewError("当前版本与基线版本相同，无需复验")
    quota = max(0, int(quota))

    baseline = _lines(baseline_version)
    current = _lines(current_version)
    candidate_quotes: list[str] = []
    values: dict[str, dict[str, Any]] = {}
    for quote_id in sorted(set(baseline) | set(current)):
        base_exists = quote_id in baseline
        curr_exists = quote_id in current
        base_value = baseline.get(quote_id, {}).get(field)
        curr_value = current.get(quote_id, {}).get(field)
        if base_exists and curr_exists and _same(field, base_value, curr_value):
            continue
        candidate_quotes.append(quote_id)
        values[quote_id] = {
            "报价编号": quote_id,
            "当前值": curr_value,
            "基线值": base_value,
            "基线存在": base_exists,
            "当前存在": curr_exists,
        }

    tickets = [
        row for row in _active_tickets()
        if row.get("字段名") == field
        and row.get("状态") in ("待取样", "已取样")
        # 复核单的基线必须与本次复验基线一致，否则前值不可直接复用
        and str(row.get("基线版本")) == baseline_version
    ]
    ticket_by_quote: dict[str, dict[str, Any]] = {}
    for ticket in sorted(tickets, key=lambda row: int(row.get("id", 0))):
        ticket_by_quote.setdefault(str(ticket.get("报价编号")), ticket)

    candidate_set = set(candidate_quotes)
    reserved = [qid for qid in ticket_by_quote if qid in candidate_set]
    normal = [qid for qid in candidate_quotes if qid not in ticket_by_quote]
    ordered = reserved + normal
    selected = ordered[:quota]

    sampled: list[dict[str, Any]] = []
    not_sampled: list[dict[str, Any]] = []

    for quote_id in ordered:
        info = values[quote_id]
        ticket = ticket_by_quote.get(quote_id)
        if quote_id in selected:
            if ticket is not None:
                reason = (
                    f"复核单 {ticket.get('复核单编号')} 锁定后当前 {field} 仍为 "
                    f"{info['当前值']}，与基线 {baseline_version} 的 {info['基线值']} 仍有差异，"
                    "占用本轮复验名额"
                )
            else:
                reason = (
                    f"当前 {field} 为 {info['当前值']}，与基线 {baseline_version} 的 "
                    f"{info['基线值']} 仍有差异，占用本轮复验名额"
                )
            entry = _ledger_entry(
                ticket_id=str(ticket.get("复核单编号")) if ticket else "",
                quote_id=quote_id,
                field=field,
                sample_value=info["当前值"],
                published_version=current_version,
                baseline_value=info["基线值"],
                baseline_version=baseline_version,
                sample_type="当前复验",
                included=True,
                result="差异成立",
                reason=reason,
                operator=operator,
            )
            sampled.append(entry)
        else:
            reason = (
                f"超出本轮复验名额 {quota}；名额优先保留给已开复核单且仍有差异的报价"
            )
            entry = _ledger_entry(
                ticket_id=str(ticket.get("复核单编号")) if ticket else "",
                quote_id=quote_id,
                field=field,
                sample_value=info["当前值"],
                published_version=current_version,
                baseline_value=info["基线值"],
                baseline_version=baseline_version,
                sample_type="当前复验",
                included=False,
                result="未入样",
                reason=reason,
                operator=operator,
            )
            not_sampled.append(entry)

    # 复核单对应的报价已更正回基线（或已从当前批次消失）：不入样，但必须写明更正依据。
    # 若更正批又把它改成与基线不同的值，它已经进入上面的候选集合并按名额竞争。
    reverted: list[dict[str, Any]] = []
    for quote_id, ticket in ticket_by_quote.items():
        if quote_id in candidate_set:
            continue
        exists, live_value = _value_of(current_version, quote_id, field)
        baseline_value = baseline.get(quote_id, {}).get(field)
        if not exists:
            reason = f"更正批 {current_version} 中已无该报价，无法复验"
        else:
            reason = (
                f"更正批 {current_version} 已将{field}由复核单快照 {ticket.get('后值')} "
                f"更正为 {live_value}，与基线 {baseline_version} 的 {baseline_value} 一致；"
                f"原 {ticket.get('来源版本')} 样本保留，不占本轮复验名额"
            )
            ticket["更正依据"] = reason
        entry = _ledger_entry(
            ticket_id=str(ticket.get("复核单编号")),
            quote_id=quote_id,
            field=field,
            sample_value=live_value,
            published_version=current_version,
            baseline_value=baseline_value,
            baseline_version=baseline_version,
            sample_type="当前复验",
            included=False,
            result="未入样",
            reason=reason,
            operator=operator,
        )
        reverted.append(entry)

    anomaly_quotes = [row["报价编号"] for row in sampled]

    plan = _record_quality_plan(
        current_version=current_version,
        field=field,
        sampled=len(sampled),
        candidates=len(candidate_quotes),
        not_sampled=len(not_sampled) + len(reverted),
        abnormal=bool(sampled),
        operator=operator,
    )

    old_samples = [
        dict(row) for row in store.rows("quality_ledger")
        if row.get("样本类型") == "复核单取样"
        and str(row.get("报价编号")) in ticket_by_quote
        and row.get("字段名") == field
    ]

    return {
        "ok": True,
        "field": field,
        "baseline_version": baseline_version,
        "current_version": current_version,
        "quota": quota,
        "candidate_count": len(candidate_quotes),
        "candidates": [values[qid] for qid in ordered],
        "sampled": sampled,
        "not_sampled": not_sampled + reverted,
        "anomaly_quotes": anomaly_quotes,
        "old_samples": old_samples,
        "quality_plan": plan,
        "message": (
            f"{field}当前复验完成：候选{len(candidate_quotes)}条，"
            f"入样{len(sampled)}条，未入样{len(not_sampled) + len(reverted)}条"
        ),
    }


# ---------------------------------------------------------------------------
# 质检台账查询
# ---------------------------------------------------------------------------

def list_ledger(
    *,
    field: str | None = None,
    quote_id: str | None = None,
    ticket_id: str | None = None,
    sample_type: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    rows = list(store.rows("quality_ledger"))
    if field:
        rows = [row for row in rows if row.get("字段名") == field]
    if quote_id:
        rows = [row for row in rows if str(row.get("报价编号")) == str(quote_id)]
    if ticket_id:
        rows = [row for row in rows if str(row.get("复核单编号")) == str(ticket_id)]
    if sample_type:
        rows = [row for row in rows if row.get("样本类型") == sample_type]
    rows.sort(key=lambda row: int(row.get("id", 0)))
    total = len(rows)
    page = max(1, int(page))
    size = max(1, min(int(size), 200))
    start = (page - 1) * size
    return [{key: row.get(key) for key in LEDGER_FIELDS} for row in rows[start:start + size]], total


# ---------------------------------------------------------------------------
# 报价发布轨迹
# ---------------------------------------------------------------------------

def quote_history(quote_id: str, *, field: str | None = None) -> dict[str, Any]:
    """回查一条报价在各发布版本中的值，说明上午那份报价是怎么发出去的。"""
    quote_id = str(quote_id or "").strip()
    if not quote_id:
        raise QuoteReviewError("必须指定报价编号")
    versions = _published_versions()
    if not versions:
        raise QuoteReviewError("尚未发布任何报价批次")

    timeline: list[dict[str, Any]] = []
    previous_version: str | None = None
    previous_value: Any = None
    previous_exists = False
    for batch in versions:
        version = str(batch["批次版本"])
        row = _lines(version).get(quote_id)
        exists = row is not None
        value = row.get(field) if exists and field else None
        if field:
            changed = exists != previous_exists or not _same(field, value, previous_value)
        else:
            changed = None
        timeline.append({
            "批次版本": version,
            "父版本": str(batch.get("父版本") or ""),
            "存在": exists,
            "值": value,
            "相比上一发布版本有变化": changed,
            "上一版本": previous_version,
            "发布时间": batch.get("发布时间"),
        })
        previous_version, previous_value, previous_exists = version, value, exists

    related_diffs = [
        {key: row.get(key) for key in DIFF_FIELDS}
        for row in store.rows("quote_diff")
        if str(row.get("报价编号")) == quote_id and (field is None or row.get("字段名") == field)
    ]
    related_tickets = [
        {key: row.get(key) for key in TICKET_FIELDS}
        for row in store.rows("review_ticket")
        if str(row.get("报价编号")) == quote_id and (field is None or row.get("字段名") == field)
    ]
    related_samples = [
        {key: row.get(key) for key in LEDGER_FIELDS}
        for row in store.rows("quality_ledger")
        if str(row.get("报价编号")) == quote_id and (field is None or row.get("字段名") == field)
    ]
    return {
        "报价编号": quote_id,
        "字段名": field,
        "timeline": timeline,
        "diffs": related_diffs,
        "tickets": related_tickets,
        "samples": related_samples,
    }


# ---------------------------------------------------------------------------
# 本地演示批次
# ---------------------------------------------------------------------------

DEMO_BASELINE = [
    {"报价编号": "A", "金额": 8, "标题": "A-骨架屏采集"},
    {"报价编号": "B", "金额": 12, "标题": "B-旧标题"},
    {"报价编号": "C", "金额": 6, "标题": "C-分页脚本"},
    {"报价编号": "D", "金额": 5, "标题": "D-代理池扩容"},
]

# V1：A、D 金额变化，B 只改标题，C 原样
DEMO_V1 = [
    {"报价编号": "A", "金额": 9, "标题": "A-骨架屏采集"},
    {"报价编号": "B", "金额": 12, "标题": "B-新标题"},
    {"报价编号": "C", "金额": 6, "标题": "C-分页脚本"},
    {"报价编号": "D", "金额": 7, "标题": "D-代理池扩容"},
]

# V2：只把 A 更正回 8
DEMO_V2 = [
    {"报价编号": "A", "金额": 8, "标题": "A-骨架屏采集"},
    {"报价编号": "B", "金额": 12, "标题": "B-新标题"},
    {"报价编号": "C", "金额": 6, "标题": "C-分页脚本"},
    {"报价编号": "D", "金额": 7, "标题": "D-代理池扩容"},
]


def reset_quote_domain() -> None:
    for module in ("quote_batch", "quote_line", "quote_diff", "review_ticket", "quality_ledger"):
        store.rows(module).clear()


def bootstrap_demo() -> dict[str, Any]:
    """准备本地演示：重置报价域，发布 V0 基线批和 V1 上午批。"""
    reset_quote_domain()
    baseline = publish_batch("V0", DEMO_BASELINE, operator="sys")
    morning = publish_batch("V1", DEMO_V1, parent_version="V0", operator="值班员")
    return {
        "ok": True,
        "batches": [baseline["batch"], morning["batch"]],
        "diffs": morning["diffs"],
        "message": "本地报价批次已准备：V1 含 A、D 金额变化和 B 标题变化",
    }


def publish_demo_correction() -> dict[str, Any]:
    """发布 V2 更正批：把 A 的金额改回 8。"""
    if _version_row("V2") is not None:
        raise QuoteReviewError("演示更正批 V2 已存在")
    result = publish_batch("V2", DEMO_V2, parent_version="V1", operator="值班员")
    return {
        "ok": True,
        "batch": result["batch"],
        "diffs": result["diffs"],
        "message": "更正批 V2 已发布：A 的金额由 9 改回 8",
    }
