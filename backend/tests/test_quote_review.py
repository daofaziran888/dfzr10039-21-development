"""报价差异复核：按字段查差异 → 开复核单 → 按原单取样 → 当前复验与台账。"""
from __future__ import annotations

import pytest

from app.services import quote_review


@pytest.fixture()
def demo() -> dict:
    return quote_review.bootstrap_demo()


def test_field_diff_carries_before_after_and_source_version(demo: dict) -> None:
    items, total = quote_review.query_field_diffs(field="金额")

    assert total == 2
    assert [row["报价编号"] for row in items] == ["A", "D"]
    by_id = {row["报价编号"]: row for row in items}
    assert by_id["A"]["前值"] == 8
    assert by_id["A"]["后值"] == 9
    assert by_id["A"]["来源版本"] == "V1"
    assert by_id["A"]["基线版本"] == "V0"
    assert by_id["D"]["前值"] == 5 and by_id["D"]["后值"] == 7
    assert all(row["字段名"] == "金额" for row in items)


def test_title_query_only_returns_title_change(demo: dict) -> None:
    items, total = quote_review.query_field_diffs(field="标题")

    assert total == 1
    assert items[0]["报价编号"] == "B"
    assert items[0]["字段名"] == "标题"
    assert items[0]["前值"] == "B-旧标题"
    assert items[0]["后值"] == "B-新标题"


def test_field_diff_pagination(demo: dict) -> None:
    first, total = quote_review.query_field_diffs(field="金额", page=1, size=1)
    second, _ = quote_review.query_field_diffs(field="金额", page=2, size=1)

    assert total == 2
    assert [row["报价编号"] for row in first] == ["A"]
    assert [row["报价编号"] for row in second] == ["D"]


def test_review_ticket_snapshots_field_values_and_source(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    result = quote_review.create_review_tickets([diffs[0]["id"]])

    assert result["ok"] is True
    ticket = result["created"][0]
    assert ticket["复核单编号"] == "RVT-0001"
    assert ticket["报价编号"] == "A"
    assert ticket["字段名"] == "金额"
    assert ticket["前值"] == 8
    assert ticket["后值"] == 9
    assert ticket["来源版本"] == "V1"
    assert ticket["基线版本"] == "V0"
    assert ticket["状态"] == "待取样"


def test_duplicate_submission_keeps_single_effective_ticket(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    quote_review.create_review_tickets([diffs[0]["id"]])

    again = quote_review.create_review_tickets(field="金额", quote_id="A")
    by_key = quote_review.create_review_tickets(
        field="金额", quote_id="A", idempotency_key="morning-A-amount",
    )
    by_key_again = quote_review.create_review_tickets(
        field="金额", quote_id="A", idempotency_key="morning-A-amount",
    )

    assert again["duplicate"] is True
    assert again["created"] == []
    assert len(again["reused"]) == 1
    assert by_key_again["duplicate"] is True
    assert by_key_again["created"] == []
    assert len([row for row in quote_review.store.rows("review_ticket") if row["报价编号"] == "A"]) == 1


def test_original_ticket_snapshot_survives_correction(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    ticket_id = quote_review.create_review_tickets([diffs[0]["id"]])["created"][0]["复核单编号"]

    quote_review.publish_demo_correction()
    detail = quote_review.get_review_ticket(ticket_id)

    assert detail["snapshot"]["前值"] == 8
    assert detail["snapshot"]["后值"] == 9
    assert detail["snapshot"]["来源版本"] == "V1"
    assert detail["live"]["当前版本"] == "V2"
    assert detail["live"]["当前值"] == 8
    assert detail["version_changed"] is True
    assert "原单快照保持不变" in detail["note"]
    assert "更正为 8" in detail["note"]


def test_sampling_uses_original_source_version_and_locks(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    ticket_id = quote_review.create_review_tickets([diffs[0]["id"]])["created"][0]["复核单编号"]
    quote_review.publish_demo_correction()

    first = quote_review.sample_ticket(ticket_id)
    second = quote_review.sample_ticket(ticket_id)

    assert first["sample"]["样本值"] == 9, "必须按原单来源版本 V1 取样，而不是更正后的 V2"
    assert first["sample"]["发布版本"] == "V1"
    assert first["sample"]["结果"] == "差异成立"
    assert first["sample"]["入样"] is True
    assert second["duplicate"] is True
    assert second["sample"]["样本值"] == 9
    assert second["sample"]["发布版本"] == "V1"
    assert len([r for r in quote_review.store.rows("quality_ledger") if r["复核单编号"] == ticket_id]) == 1


def test_sampling_refuses_tampered_source_version(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    ticket_id = quote_review.create_review_tickets([diffs[0]["id"]])["created"][0]["复核单编号"]

    for row in quote_review.store.rows("quote_line"):
        if row["批次版本"] == "V1" and row["报价编号"] == "A":
            row["金额"] = 11

    with pytest.raises(quote_review.QuoteVersionConflict):
        quote_review.sample_ticket(ticket_id)
    assert quote_review.store.rows("quality_ledger") == []


def test_current_reverify_does_not_count_reverted_quote_as_abnormal(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    ticket_id = quote_review.create_review_tickets([diffs[0]["id"]])["created"][0]["复核单编号"]
    quote_review.sample_ticket(ticket_id)
    quote_review.publish_demo_correction()

    result = quote_review.reverify_current(field="金额", baseline_version="V0", quota=2)

    assert result["anomaly_quotes"] == ["D"], "A 已更正回 8，不能再把 8 对 8 计作异常"
    assert [row["报价编号"] for row in result["sampled"]] == ["D"]
    assert result["sampled"][0]["样本值"] == 7
    assert result["sampled"][0]["发布版本"] == "V2"

    a_entry = next(row for row in result["not_sampled"] if row["报价编号"] == "A")
    assert a_entry["入样"] is False
    assert a_entry["结果"] == "未入样"
    assert "更正批 V2" in a_entry["理由"]
    assert "9 更正为 8" in a_entry["理由"]

    old = result["old_samples"][0]
    assert old["报价编号"] == "A"
    assert old["样本值"] == 9
    assert old["发布版本"] == "V1", "旧样本必须标明发布版本"
    assert old["台账编号"] != a_entry["台账编号"]

    assert result["quality_plan"]["status"] == "偏差告警"
    assert result["quality_plan"]["目标批次"] == "V2"


def test_reverify_quota_reserved_for_ticket_holders_with_live_diff(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额")
    by_quote = {row["报价编号"]: row for row in diffs}
    ticket_id = quote_review.create_review_tickets([by_quote["D"]["id"]])["created"][0]["复核单编号"]
    quote_review.sample_ticket(ticket_id)

    result = quote_review.reverify_current(field="金额", baseline_version="V0", quota=1)

    assert [row["报价编号"] for row in result["sampled"]] == ["D"]
    a_entry = next(row for row in result["not_sampled"] if row["报价编号"] == "A")
    assert "超出本轮复验名额" in a_entry["理由"]


def test_correction_that_reintroduces_diff_is_not_waived(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    ticket_id = quote_review.create_review_tickets([diffs[0]["id"]])["created"][0]["复核单编号"]
    quote_review.sample_ticket(ticket_id)
    quote_review.publish_demo_correction()
    quote_review.publish_batch(
        "V3",
        [
            {"报价编号": "A", "金额": 10, "标题": "A-骨架屏采集"},
            {"报价编号": "B", "金额": 12, "标题": "B-新标题"},
            {"报价编号": "C", "金额": 6, "标题": "C-分页脚本"},
            {"报价编号": "D", "金额": 7, "标题": "D-代理池扩容"},
        ],
        parent_version="V2",
    )

    result = quote_review.reverify_current(
        field="金额", baseline_version="V0", current_version="V3", quota=2,
    )

    assert result["candidate_count"] == 2
    assert set(result["anomaly_quotes"]) == {"A", "D"}
    a_sample = next(row for row in result["sampled"] if row["报价编号"] == "A")
    assert a_sample["样本值"] == 10
    assert a_sample["发布版本"] == "V3"
    assert a_sample["结果"] == "差异成立"


def test_quota_prefers_ticket_holder_when_normal_candidate_sorts_first(demo: dict) -> None:
    """A（有复核单、仍有差异）应优先于字母序更靠前的无单报价占用名额。"""
    diffs, _ = quote_review.query_field_diffs(field="金额")
    by_quote = {row["报价编号"]: row for row in diffs}
    ticket_id = quote_review.create_review_tickets([by_quote["D"]["id"]])["created"][0]["复核单编号"]
    quote_review.sample_ticket(ticket_id)

    result = quote_review.reverify_current(field="金额", baseline_version="V0", quota=1)

    assert [row["报价编号"] for row in result["sampled"]] == ["D"]
    assert result["anomaly_quotes"] == ["D"]
    a_entry = next(row for row in result["not_sampled"] if row["报价编号"] == "A")
    assert a_entry["入样"] is False
    assert "超出本轮复验名额" in a_entry["理由"]


def test_other_baseline_ticket_is_not_reused(demo: dict) -> None:
    """基线不同的复核单不能直接套用前值，按普通候选处理。"""
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    ticket_id = quote_review.create_review_tickets([diffs[0]["id"]])["created"][0]["复核单编号"]
    quote_review.sample_ticket(ticket_id)

    quote_review.publish_batch(
        "V4",
        [
            {"报价编号": "A", "金额": 10, "标题": "A-骨架屏采集"},
            {"报价编号": "B", "金额": 12, "标题": "B-新标题"},
            {"报价编号": "C", "金额": 6, "标题": "C-分页脚本"},
            {"报价编号": "D", "金额": 8, "标题": "D-代理池扩容"},
        ],
        parent_version="V1",
    )

    result = quote_review.reverify_current(
        field="金额", baseline_version="V1", current_version="V4", quota=2,
    )

    a_entry = next(row for row in result["sampled"] if row["报价编号"] == "A")
    assert a_entry["基线版本"] == "V1"
    assert a_entry["基线值"] == 9, "应取 V1 的实际值，而不是复核单 V0 基线的前值 8"
    assert a_entry["样本值"] == 10
    assert a_entry["复核单编号"] == "", "基线不同的复核单不参与按单名额保留"


def test_ledger_filters_by_ticket_and_version_is_recorded(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    ticket_id = quote_review.create_review_tickets([diffs[0]["id"]])["created"][0]["复核单编号"]
    quote_review.sample_ticket(ticket_id)

    items, total = quote_review.list_ledger(ticket_id=ticket_id)

    assert total == 1
    assert len(items) == 1
    assert items[0]["发布版本"] == "V1"
    assert items[0]["样本值"] == 9


def test_quote_history_shows_publish_trail(demo: dict) -> None:
    diffs, _ = quote_review.query_field_diffs(field="金额", quote_id="A")
    ticket_id = quote_review.create_review_tickets([diffs[0]["id"]])["created"][0]["复核单编号"]
    quote_review.sample_ticket(ticket_id)
    quote_review.publish_demo_correction()

    history = quote_review.quote_history("A", field="金额")

    assert [(row["批次版本"], row["值"]) for row in history["timeline"]] == [
        ("V0", 8), ("V1", 9), ("V2", 8),
    ]
    assert history["timeline"][1]["父版本"] == "V0"
    assert history["diffs"][0]["后值"] == 9 and history["diffs"][0]["来源版本"] == "V1"
    assert history["tickets"][0]["复核单编号"] == ticket_id
    assert history["samples"][0]["发布版本"] == "V1"
