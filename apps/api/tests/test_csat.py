"""第 48 刀 CSAT 纯函数单测（无 DB）：`_build_csat` 的窗口聚合口径。

钉的是「空态诚实」（0 条 -> 均值 None，不返回 0.0 冒充均分）、分布恒含五档、
最近留言条数上限与**先掩后截**（先截会把手机号切成掩不住的残片）。
"""

from datetime import UTC, datetime, timedelta

from suite_api.routes.stats import (
    CSAT_COMMENT_LIMIT,
    CSAT_COMMENT_MAX_CHARS,
    _build_csat,
)

_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _rows(*specs: tuple[int, str | None], offset_minutes: int = 0) -> list[tuple]:
    """按「越靠前越新」造行（与端点里的 created_at DESC 排序一致）。

    行元组 = (session_id, score, comment, created_at)：第 53 刀起带 session_id
    （低分留言要能点回会话）。
    """
    return [
        (100 + index, score, comment, _NOW - timedelta(minutes=offset_minutes + index))
        for index, (score, comment) in enumerate(specs)
    ]


def test_build_csat_empty_is_honest() -> None:
    csat = _build_csat([])
    assert csat.ratings_last_7d == 0
    assert csat.average_last_7d is None  # 不除零、不谎报 0.0
    assert csat.distribution == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    assert csat.recent_comments == []


def test_build_csat_average_distribution_and_rounding() -> None:
    csat = _build_csat(_rows((5, None), (5, None), (3, None)))
    assert csat.ratings_last_7d == 3
    assert csat.average_last_7d == 4.3  # 13/3 -> 1 位小数
    assert csat.distribution == {"1": 0, "2": 0, "3": 1, "4": 0, "5": 2}
    # 均分必须配分布看：4.3 也可能是 5+5+3，而不是「都不错」
    assert csat.recent_comments == []


def test_build_csat_unknown_score_is_ignored_everywhere() -> None:
    """越界分（应用层写不出来，手改库才可能有）**三处口径统一地当它不存在**：
    不进分布、不进条数、不进均值——否则 `ratings_last_7d != sum(distribution)`、
    均值还会被越界值拉偏。"""
    csat = _build_csat(_rows((6, None), (4, None)))
    assert csat.distribution == {"1": 0, "2": 0, "3": 0, "4": 1, "5": 0}
    assert csat.ratings_last_7d == 1  # 与分布之和一致
    assert csat.average_last_7d == 4.0  # 只由合法分算


def test_build_csat_average_rounds_half_up() -> None:
    """4.25 显示 4.3（四舍五入，不是 Python round 的银行家舍入 4.2）。"""
    csat = _build_csat(_rows((5, None), (5, None), (4, None), (3, None)))  # 17/4
    assert csat.average_last_7d == 4.3


def test_build_csat_masks_before_truncating() -> None:
    """先掩后截——把号码**摆到 60 字截断线上**才有鉴别力。

    号码整体在截断线内时，「先截后掩」与「先掩后截」输出逐字相同（旧用例是伪钉）。
    跨界后两者可分：先截会留下「13800」这样的**残片**（不足 7 位、掩不住），
    先掩则在截断前已变成 `1********00`（截断只砍掉掩码尾巴，`1****`）。
    """
    comment = "前" * 55 + "13800138000"
    csat = _build_csat(_rows((2, comment)))
    text = csat.recent_comments[0].comment
    assert len(text) <= CSAT_COMMENT_MAX_CHARS
    assert "138" not in text  # 先截后掩会在这里留下 13800 残片 -> 红
    assert "*" in text  # 掩码真的先发生了（截断砍在掩码上）


def test_build_csat_masks_inside_the_limit() -> None:
    """号码在截断线内：整段掩码完整出现（常规路径）。"""
    csat = _build_csat(_rows((2, "帮我回电 13800138000")))
    text = csat.recent_comments[0].comment
    assert "13800138000" not in text
    assert "1********00" in text


def test_build_csat_comment_limit_and_blank_skipping() -> None:
    csat = _build_csat(
        _rows((5, "第一条"), (4, "   "), (3, "第三条"), (2, "第四条"), (1, "第五条"))
    )
    assert [c.comment for c in csat.recent_comments] == ["第一条", "第三条", "第四条"]
    assert len(csat.recent_comments) == CSAT_COMMENT_LIMIT


def test_build_csat_comments_carry_session_id_and_score() -> None:
    """第 53 刀：留言带 session_id/score——低分留言要能点回那次会话（此前只有文本）。"""
    csat = _build_csat(_rows((2, "物流太慢")))
    entry = csat.recent_comments[0]
    assert entry.session_id == 100  # 造数助手给的第一条
    assert entry.score == 2
    assert entry.comment == "物流太慢"
