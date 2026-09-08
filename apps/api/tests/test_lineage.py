"""血缘视图单元测试（不依赖 DB；第 20 刀/ADR 0026）。

覆盖两块：
- 纯拼装 assemble_lineage：三源聚合（audit 时间线/发布行导出写回/考核题源锚）、
  版本号从 JSONB 原样提取（提不出跳样例不跳计数、bool 不算整数）、问句与题面
  60 字截断、引用样例硬上限 10、空态（三块全空）、写回不带 fields 键、
  origin.created_at 恒 null（assets 无登记时间列，不发明时间）。
- 查询形状下推：citations/coaching 的 containment 编译进 PostgreSQL 方言后
  必须出现 ``@>``（JSONB containment 下推，禁全表拉回 Python 过滤），
  样例语句带 LIMIT。
"""

from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from suite_api.services.lineage import (
    SAMPLE_LIMIT,
    assemble_lineage,
    citation_count_stmt,
    citation_sample_stmt,
    coaching_stmt,
)

_AT0 = datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC)


def _at(i: int) -> datetime:
    return datetime(2026, 9, 1 + i // 24, 8, i % 24, 0, tzinfo=UTC)


def _citation_row(i: int, asset_id: int = 9, version_no: int = 1):
    return (100 + i, f"问句{i}", [{"asset_id": asset_id, "version_no": version_no}], _at(i))


def _assemble(**overrides):
    kwargs: dict = {
        "asset_id": 9,
        "source_kind": "upload",
        "product_id": 3,
        "audit_rows": [
            (_at(3), "publish", 2, "operator"),
            (_at(2), "confirm", 2, "operator"),
            (_at(1), "publish", 1, "operator"),
        ],
        "citation_rows": [_citation_row(2), _citation_row(1)],
        "citations_total": 2,
        "coach_rows": [
            (7, "盲盒可以指定款式吗", {"asset_id": 9, "version_no": 1, "source": "qa", "pair_index": 0}, _at(5)),
        ],
    }
    kwargs.update(overrides)
    return assemble_lineage(**kwargs)


# ---------- 三源聚合 ----------


def test_assemble_aggregates_three_sources_in_order() -> None:
    out = _assemble()
    # 时间线保持查询给的倒序（audit_log id 倒序），action/version/操作者名逐行拼
    assert [(e.action, e.version_no, e.operator) for e in out.versions_audit] == [
        ("publish", 2, "operator"),
        ("confirm", 2, "operator"),
        ("publish", 1, "operator"),
    ]
    # 写回=发布事件（0010 同事务）：confirm/rollback 行不进；带商品锚与操作者
    assert [(w.version_no, w.operator, w.product_id) for w in out.usages.writebacks] == [
        (2, "operator", 3),
        (1, "operator", 3),
    ]
    # audit_log 不存字段名：如实不带 fields 键（契约形状钉死）
    assert "fields" not in out.usages.writebacks[0].model_dump()
    # 引用来料即样料：会话/问句/版本/时间
    assert [(c.session_id, c.question, c.version_no) for c in out.usages.citations.samples] == [
        (102, "问句2", 1),
        (101, "问句1", 1),
    ]
    assert out.usages.citations.total == 2
    # 考核：题源锚里的版本号原样带出
    assert [(c.record_id, c.question, c.version_no) for c in out.usages.coaching] == [
        (7, "盲盒可以指定款式吗", 1),
    ]
    # origin：来源种类如实；登记时间 assets 没存列 -> 恒 null
    assert out.origin.source_kind == "upload"
    assert out.origin.created_at is None


def test_writeback_product_null_for_unlinked_asset() -> None:
    out = _assemble(product_id=None)
    assert all(w.product_id is None for w in out.usages.writebacks)


def test_empty_usages_is_the_empty_state() -> None:
    out = _assemble(audit_rows=[], citation_rows=[], citations_total=0, coach_rows=[])
    assert out.versions_audit == []
    assert out.usages.citations.total == 0
    assert out.usages.citations.samples == []
    assert out.usages.writebacks == []
    assert out.usages.coaching == []


# ---------- 截断与上限 ----------


def test_question_and_prompt_truncated_to_60_chars() -> None:
    long_q = "退" * 100
    out = _assemble(
        citation_rows=[(101, long_q, [{"asset_id": 9, "version_no": 1}], _at(1))],
        citations_total=1,
        coach_rows=[(7, long_q, {"asset_id": 9, "version_no": 1}, _at(5))],
    )
    assert out.usages.citations.samples[0].question == "退" * 60 + "…"
    assert out.usages.coaching[0].question == "退" * 60 + "…"
    # 恰好不超限不加省略号
    out2 = _assemble(
        citation_rows=[(101, "退" * 60, [{"asset_id": 9, "version_no": 1}], _at(1))],
        citations_total=1,
        coach_rows=[],
    )
    assert out2.usages.citations.samples[0].question == "退" * 60


def test_citation_samples_capped_at_sample_limit() -> None:
    rows = [_citation_row(i) for i in range(SAMPLE_LIMIT + 5)]
    out = _assemble(citation_rows=rows, citations_total=SAMPLE_LIMIT + 5)
    assert len(out.usages.citations.samples) == SAMPLE_LIMIT
    # 倒序来料：留下的仍是最新一批（首条=第一行）
    assert out.usages.citations.samples[0].question == "问句0"
    # 计数与样例上限无关：total 如实透传
    assert out.usages.citations.total == SAMPLE_LIMIT + 5


def test_missing_question_falls_back_to_dash() -> None:
    out = _assemble(
        citation_rows=[(101, None, [{"asset_id": 9, "version_no": 1}], _at(1))],
        citations_total=1,
    )
    assert out.usages.citations.samples[0].question == "—"


# ---------- JSONB 值的防御：提不出版本就不编造 ----------


def test_citation_row_without_our_asset_version_is_skipped() -> None:
    rows = [
        (101, "他山问句", [{"asset_id": 8, "version_no": 3}], _at(3)),  # 不含本资产
        (102, "坏版本", [{"asset_id": 9, "version_no": True}], _at(2)),  # bool 不算整数
        (103, "无版本", [{"asset_id": 9}], _at(1)),  # 缺 version_no
        _citation_row(0),  # 唯一合法行
    ]
    out = _assemble(citation_rows=rows, citations_total=4)
    assert out.usages.citations.total == 4  # 计数是同条件查询给的，如实透传
    assert [(c.session_id, c.version_no) for c in out.usages.citations.samples] == [(100, 1)]


def test_coaching_record_without_version_in_key_is_skipped() -> None:
    out = _assemble(
        coach_rows=[
            (7, "坏锚题面", {"asset_id": 9, "source": "qa"}, _at(5)),
            (8, "主题面", {"asset_id": 9, "version_no": 2, "source": "qa", "pair_index": 1}, _at(4)),
        ],
    )
    assert [(c.record_id, c.version_no) for c in out.usages.coaching] == [(8, 2)]


# ---------- 查询形状：containment 必须下推 SQL ----------


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_citation_queries_push_down_jsonb_containment() -> None:
    for stmt in (citation_count_stmt(9), citation_sample_stmt(9)):
        sql = _sql(stmt)
        assert "citations @>" in sql, "citations 必须 JSONB containment 下推，禁全表拉回"
        assert "service_messages" in sql
    sample_sql = _sql(citation_sample_stmt(9))
    assert "LIMIT" in sample_sql  # 样例上限在 SQL 里
    assert "role = " in sample_sql  # 问句相关子查询同语句下推
    count_sql = _sql(citation_count_stmt(9))
    assert "count(" in count_sql.lower()


def test_coaching_query_pushes_down_question_key_containment() -> None:
    sql = _sql(coaching_stmt(9))
    assert "question_key @>" in sql
    assert "coach_records" in sql
