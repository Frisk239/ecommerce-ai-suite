"""scripts/realdata 纯函数单测（多来源真实数据刀 I）：离线，不吃外网不连库。

覆盖：SPARQL 查询构造（六类 QID / UNION / 每类 LIMIT / 标签回退链）、SPARQL
json→商品行（QID 兜底标签与缺标签过滤、name+category 去重、stock 固定种子、
limit 截断、列宽截断）、评论 csv 解析（表头定位与 BOM、空评论/缺列跳过）、zip
读取（utf-8 与 gb18030 兜底）、(title, content) 转换（spec 形状）、抽样与分批
（200 对齐 API 单批上限），以及关键交叉验证：生成的批字节能被既有
parse_import_csv（上传通道批量形态）直接受理。
"""

import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from suite_api.services.csv_import import parse_import_csv

REALDATA_DIR = Path(__file__).resolve().parents[3] / "scripts" / "realdata"
SAMPLES_DIR = REALDATA_DIR / "samples"
sys.path.insert(0, str(REALDATA_DIR))

import fetch_wikidata_products as fwp  # noqa: E402
import load_reviews as lr  # noqa: E402


def _load_sparql_sample() -> dict:
    return json.loads(
        (SAMPLES_DIR / "wikidata_sparql_sample.json").read_text(encoding="utf-8")
    )


def _reviews_sample_text() -> str:
    return (SAMPLES_DIR / "online_shopping_reviews_sample.csv").read_text(encoding="utf-8")


# ---------------------------------------------------------------- SPARQL 查询构造


def test_sparql_query_shape() -> None:
    """六类 QID/中文名全部入查询；UNION 段数=类目数-1；每类 LIMIT；标签回退链。"""
    query = fwp.build_sparql_query()
    for qid, zh_name in fwp.CATEGORIES:
        assert f"wd:{qid}" in query
        assert f'BIND("{zh_name}" AS ?category)' in query  # 常量走 BIND
    assert query.count("UNION") == len(fwp.CATEGORIES) - 1
    assert query.count(f"LIMIT {fwp.DEFAULT_PER_CATEGORY}") == len(fwp.CATEGORIES)
    assert fwp.LABEL_LANGUAGES in query  # zh 优先，en 兜底
    assert "SERVICE wikibase:label" in query


def test_sparql_query_respects_per_category() -> None:
    query = fwp.build_sparql_query(per_category=7)
    assert query.count("LIMIT 7") == len(fwp.CATEGORIES)


# ---------------------------------------------------------------- SPARQL json → 商品行


def test_sparql_sample_fixture_converts() -> None:
    """50 bindings 样本：-2 QID 兜底标签 -1 缺标签 -1 重复 = 46 行；stock 可复现。"""
    rows = fwp.sparql_json_to_rows(_load_sparql_sample())
    assert len(rows) == 46
    assert all(row["name"] and row["category"] for row in rows)
    assert all(row["spec_schema"] == {} and row["spec_values"] == {} for row in rows)
    assert all(0 <= row["stock"] <= 99 for row in rows)
    names_by_cat = {(row["name"], row["category"]) for row in rows}
    assert len(names_by_cat) == len(rows)  # name+category 唯一
    again = fwp.sparql_json_to_rows(_load_sparql_sample())
    assert [row["stock"] for row in again] == [row["stock"] for row in rows]  # 种子固定


def test_sparql_rows_respect_limit() -> None:
    rows = fwp.sparql_json_to_rows(_load_sparql_sample(), limit=10)
    assert len(rows) == 10


def test_qid_fallback_and_missing_label_filtered() -> None:
    """label 服务无标签时回填 QID 串——与条目 QID 相同即判非可用商品名。"""
    payload = {
        "results": {
            "bindings": [
                {
                    "category": {"value": "智能手机"},
                    "item": {"value": "http://www.wikidata.org/entity/Q1"},
                    "itemLabel": {"value": "正常商品"},
                },
                {
                    "category": {"value": "智能手机"},
                    "item": {"value": "http://www.wikidata.org/entity/Q2"},
                    "itemLabel": {"value": "Q2"},  # 兜底 QID 串
                },
                {
                    "category": {"value": "图书"},
                    "item": {"value": "http://www.wikidata.org/entity/Q3"},
                    # 缺 itemLabel
                },
            ]
        }
    }
    rows = fwp.sparql_json_to_rows(payload)
    assert [row["name"] for row in rows] == ["正常商品"]


def test_name_truncated_to_model_width() -> None:
    long_name = "超" * 300
    payload = {
        "results": {
            "bindings": [
                {
                    "category": {"value": "图书"},
                    "item": {"value": "http://www.wikidata.org/entity/Q9"},
                    "itemLabel": {"value": long_name},
                }
            ]
        }
    }
    row = fwp.sparql_json_to_rows(payload)[0]
    assert len(row["name"]) == fwp.NAME_MAX
    assert row["name"] == long_name[: fwp.NAME_MAX]


def test_products_csv_roundtrip(tmp_path: Path) -> None:
    """写出 CSV（utf-8-sig+JSON 串 spec 列）可原样读回，列序与模型字段对齐。"""
    rows = fwp.sparql_json_to_rows(_load_sparql_sample(), limit=5)
    out = tmp_path / "products.csv"
    fwp.write_products_csv(rows, out)
    with open(out, encoding="utf-8-sig", newline="") as handle:
        read_back = list(csv.reader(handle))
    assert tuple(read_back[0]) == fwp.CSV_COLUMNS
    assert len(read_back) == 6
    assert read_back[1][0] == rows[0]["name"]
    assert json.loads(read_back[1][2]) == {}
    assert int(read_back[1][4]) == rows[0]["stock"]


# ---------------------------------------------------------------- 评论 csv 解析 / zip


def test_reviews_sample_fixture_parses() -> None:
    """50 数据行样本：-1 空评论 -1 缺列 = 48 行，(cat, label, review) 三元组。"""
    rows = lr.parse_reviews_csv(_reviews_sample_text())
    assert len(rows) == 48
    assert rows[0] == ("书籍", "1", "印刷清晰，纸张手感很好，孩子很喜欢看，值得购买。")
    assert all(cat and review for cat, _, review in rows)


def test_parse_reviews_csv_tolerates_bom() -> None:
    text = "\ufeffcat,label,review\n书籍,1,很好看\n"
    assert lr.parse_reviews_csv(text) == [("书籍", "1", "很好看")]


def _zip_bytes(csv_text: str, encoding: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("online_shopping_10_cats.csv", csv_text.encode(encoding))
    return buffer.getvalue()


def test_read_reviews_from_zip_utf8() -> None:
    rows = lr.read_reviews_from_zip(_zip_bytes(_reviews_sample_text(), "utf-8"))
    assert len(rows) == 48


def test_read_reviews_from_zip_gb18030_fallback() -> None:
    """源数据集历史编码为 GB2312 系：utf-8 解码失败自动退 gb18030（其超集）。"""
    text = "cat,label,review\n书籍,1,书页发黄但内容经典，值得收藏\n水果,0,香蕉到货已熟透\n"
    rows = lr.read_reviews_from_zip(_zip_bytes(text, "gb18030"))
    assert rows[0][2] == "书页发黄但内容经典，值得收藏"


def test_read_reviews_from_zip_rejects_unknown_encoding() -> None:
    """两种编码都失败时显式报错（GB2312/GBK 已被 gb18030 覆盖，只剩真异常）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("reviews.csv", b"\xff\xfe\x00i\x00n\x00v\x00a\x00l\x00i\x00d")
    with pytest.raises(ValueError, match="GB18030"):
        lr.read_reviews_from_zip(buffer.getvalue())


# ---------------------------------------------------------------- 抽样 / 转换 / 分批


def test_sample_reviews_deterministic_and_capped() -> None:
    rows = lr.parse_reviews_csv(_reviews_sample_text())
    first = lr.sample_reviews(rows, 10, seed=42)
    second = lr.sample_reviews(rows, 10, seed=42)
    assert first == second
    assert len(first) == 10
    assert set(first) <= set(rows)
    assert lr.sample_reviews(rows, 10_000) == rows  # n 超总量=全量


def test_review_to_title_content_shape() -> None:
    """spec 形状：title=`{类目}评论 · {前 18 字}`；content=全文（剥首尾空白）。"""
    review = "  物流很快，第二天就到了，包装严实没有磕碰，印刷清晰，值得回购  "
    title, content = lr.review_to_title_content("书籍", review)
    assert title == f"书籍评论 · {review.strip()[:18]}"
    assert content == review.strip()
    short = "很好"
    title2, content2 = lr.review_to_title_content("水果", short)
    assert title2 == "水果评论 · 很好"  # 短于 18 字取全文
    assert content2 == "很好"


def test_iter_batches_aligns_with_api_limit() -> None:
    """批大小默认 200，与 import_csv 单次行数上限一字不差对齐。"""
    rows = [("t", "c")] * 401
    batches = list(lr.iter_batches(rows))
    assert [len(batch) for batch in batches] == [200, 200, 1]
    assert list(lr.iter_batches(rows[:200])) == [rows[:200]]
    with pytest.raises(ValueError, match="批大小"):
        list(lr.iter_batches(rows, size=0))


def test_batch_bytes_accepted_by_api_parser() -> None:
    """交叉验证：脚本产的批字节能被既有上传通道解析器直接受理（0 跳过）。"""
    rows = lr.parse_reviews_csv(_reviews_sample_text())
    converted = [lr.review_to_title_content(cat, review) for cat, _, review in rows]
    valid, skipped = parse_import_csv(lr.build_batch_bytes(converted))
    assert skipped == []
    assert len(valid) == len(converted)
    assert (valid[0].title, valid[0].content) == converted[0]
    # 满批 200 行也正好压在上限内（第 201 行才会被 API 整批拒绝）
    full = [("标题", "内容")] * 200
    valid_full, skipped_full = parse_import_csv(lr.build_batch_bytes(full))
    assert len(valid_full) == 200 and skipped_full == []
