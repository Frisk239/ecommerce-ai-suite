"""scripts/realdata 纯函数单测（多来源真实数据刀 I+II）：离线，不吃外网不连库。

覆盖：SPARQL 查询构造（六类 QID / UNION / 每类 LIMIT / 标签回退链）、SPARQL
json→商品行（QID 兜底标签与缺标签过滤、name+category 去重、stock 固定种子、
limit 截断、列宽截断）、评论 csv 解析（表头定位与 BOM、空评论/缺列跳过）、zip
读取（utf-8 与 gb18030 兜底）、(title, content) 转换（spec 形状）、抽样与分批
（200 对齐 API 单批上限），以及关键交叉验证：生成的批字节能被既有
parse_import_csv（上传通道批量形态）直接受理。
刀 II 增：ABCD json→会话行（三切分展平、action 轮丢弃）→转写（顾客：/客服：
与回流端点同构）→title（scene+首问截断）；WANDS TSV 解析→Exact join
（缺行丢弃、重复去重）→切片候选行（transcript 形状、合成 timecode 自增）。
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
import load_abcd_dialogues as lad  # noqa: E402
import load_openfoodfacts as loff  # noqa: E402
import load_reviews as lr  # noqa: E402
import load_wands_clips as lwc  # noqa: E402


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
    assert "wdt:P176" in query and "wdt:P2067" in query  # 制造商 / 质量，有值才进规格正文


def test_sparql_query_respects_per_category() -> None:
    query = fwp.build_sparql_query(per_category=7)
    assert query.count("LIMIT 7") == len(fwp.CATEGORIES)


# ---------------------------------------------------------------- SPARQL json → 商品行


def test_sparql_sample_fixture_converts() -> None:
    """50 bindings 样本：-2 QID 兜底标签 -1 缺标签 -1 重复 = 46 行；stock 可复现。"""
    rows = fwp.sparql_json_to_rows(_load_sparql_sample())
    assert len(rows) == 46
    assert all(row["name"] and row["category"] for row in rows)
    assert all(row["spec_values"] == {} for row in rows)
    assert all(row["spec_schema"] for row in rows)  # 类目模板非空（第 33 刀）
    phones = [row for row in rows if row["category"] == "智能手机"]
    assert phones and "品牌" in phones[0]["spec_schema"]
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
    assert json.loads(read_back[1][2]) == rows[0]["spec_schema"]
    assert int(read_back[1][4]) == rows[0]["stock"]


def test_wikidata_spec_doc_only_when_attributes_present() -> None:
    empty = fwp.wikidata_spec_doc({"manufacturer": "", "mass": ""})
    assert empty == ""
    filled = fwp.wikidata_spec_doc({"manufacturer": "Samsung", "mass": "0.18"})
    assert "品牌：Samsung" in filled
    assert "净含量：0.18 g" in filled
    assert "见包装" not in filled


def test_sparql_row_carries_manufacturer_into_spec_doc() -> None:
    payload = {
        "results": {
            "bindings": [
                {
                    "category": {"value": "智能手机"},
                    "item": {"value": "http://www.wikidata.org/entity/Q1"},
                    "itemLabel": {"value": "示例旗舰"},
                    "mfrLabel": {"value": "Samsung"},
                    "mass": {"value": "0.2"},
                }
            ]
        }
    }
    row = fwp.sparql_json_to_rows(payload)[0]
    assert row["manufacturer"] == "Samsung"
    assert "品牌：Samsung" in row["spec_doc"]
    assert row["spec_values"] == {}  # 0010 不直写


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


# ------------------------------------------------------- ABCD json → 会话行 → 转写


def _load_abcd_sample() -> dict:
    return json.loads((SAMPLES_DIR / "abcd_sample.json").read_text(encoding="utf-8"))


def test_abcd_sample_fixture_parses() -> None:
    """三切分 train→dev→test 展平共 4 会话；action 轮（系统动作）不入 turns。"""
    sessions = lad.parse_abcd_sessions(_load_abcd_sample())
    assert [s["convo_id"] for s in sessions] == [3592, 3593, 9001, 9999]
    first = sessions[0]
    assert first["flow"] == "product_defect" and first["subflow"] == "return_size"
    # 8 轮原文去掉 1 轮 action = 7 话语轮
    assert len(first["turns"]) == 7
    assert all(author in ("customer", "agent") for author, _ in first["turns"])


def test_abcd_transcript_matches_backflow_format() -> None:
    """转写=「顾客：…/客服：…」按行——与会话回流端点同一消费格式（切块按行/轮）。"""
    sessions = lad.parse_abcd_sessions(_load_abcd_sample())
    transcript = lad.transcript_of(sessions[0])
    lines = transcript.splitlines()
    assert len(lines) == 7
    assert lines[0] == "客服：Hi!"
    assert lines[2] == "顾客：Hi! I need to return an item, can you help me with that?"
    assert all(line.startswith(("顾客：", "客服：")) for line in lines)


def test_abcd_title_scene_plus_first_question() -> None:
    """title=`{flow}/{subflow} · {顾客首问截 30 字}`——客服问候不是主题，取首顾客轮。"""
    sessions = lad.parse_abcd_sessions(_load_abcd_sample())
    title = lad.title_of(sessions[0])
    question = "Hi! I need to return an item, can you help me with that?"
    assert title == f"product_defect/return_size · {question[: lad.TITLE_HEAD_CHARS]}"
    assert len(title) <= lad.TITLE_MAX
    # 无 subflow 只留 flow；无顾客轮回退首个话语轮；超长首问截 30 字
    question = "Do you have this desk in walnut?"
    assert lad.title_of(sessions[2]) == f"single_item_query · {question[: lad.TITLE_HEAD_CHARS]}"
    # 空会话无 scene 无首问 → 兜底标题
    assert lad.title_of(sessions[3]) == "ABCD 对话"


def test_abcd_title_truncated_to_model_width() -> None:
    session = {
        "convo_id": 1,
        "flow": "f" * 100,
        "subflow": "s" * 100,
        "turns": [("customer", "q" * 300)],
    }
    assert len(lad.title_of(session)) == lad.TITLE_MAX  # assets.title String(200)


def test_abcd_sampling_deterministic_and_skips_empty() -> None:
    """固定种子可复现；空 turns 会话（test 切分末位）不进抽样池；n 超量=全量。"""
    sessions = lad.parse_abcd_sessions(_load_abcd_sample())
    first = lad.sample_sessions(sessions, 2, seed=42)
    second = lad.sample_sessions(sessions, 2, seed=42)
    assert first == second
    assert len(first) == 2
    assert all(s["turns"] for s in first)
    eligible = [s for s in sessions if s["turns"]]
    assert lad.sample_sessions(sessions, 10_000) == eligible


# ------------------------------------------------- WANDS TSV 解析 / Exact join / 候选行


def _wands_texts() -> dict[str, str]:
    return {
        name: (SAMPLES_DIR / f"wands_{name}_sample.tsv").read_text(encoding="utf-8")
        for name in ("query", "product", "label")
    }


def test_wands_tsv_fixture_parses() -> None:
    """WANDS csv 实为 TSV：按 tab 列名定位成 dict 行；BOM 容忍；缺列行跳过。"""
    texts = _wands_texts()
    queries = lwc.parse_wands_tsv(texts["query"])
    assert len(queries) == 4 and queries[0]["query"] == "salon chair"
    products = lwc.parse_wands_tsv(texts["product"])
    assert products[1]["product_name"] == "all-clad 7 qt. slow cooker"
    assert len(lwc.parse_wands_tsv(texts["label"])) == 9
    assert lwc.parse_wands_tsv("\ufeffquery_id\tquery\n1\trug\n") == [
        {"query_id": "1", "query": "rug"}
    ]
    assert lwc.parse_wands_tsv("query_id\tquery\n1\n") == []  # 缺列行跳过


def test_wands_join_exact_pairs_only() -> None:
    """只留 Exact；缺 product/query 行丢弃；(query_id, product_id) 重复去重。"""
    texts = _wands_texts()
    pairs = lwc.join_exact_pairs(
        lwc.parse_wands_tsv(texts["query"]),
        lwc.parse_wands_tsv(texts["product"]),
        lwc.parse_wands_tsv(texts["label"]),
    )
    # 9 标注行：Exact 6 行中 -1 缺商品(99999) -1 缺 query(4) -1 重复(2,0) = 3 对
    assert [(p["query_id"], p["product_id"]) for p in pairs] == [
        ("0", "25434"),
        ("1", "12088"),
        ("2", "0"),
    ]
    assert pairs[0]["query"] == "salon chair"
    assert pairs[0]["product_name"] == "relaxzen massage chair"
    assert pairs[0]["query_class"] == "Massage Chairs"
    assert pairs[2]["product_class"] == "Beds"


def test_wands_candidate_row_shape() -> None:
    """transcript=`顾客问 {query} —— {product_name}（标注：Exact）`；timecode 合成自增。"""
    texts = _wands_texts()
    pairs = lwc.join_exact_pairs(
        lwc.parse_wands_tsv(texts["query"]),
        lwc.parse_wands_tsv(texts["product"]),
        lwc.parse_wands_tsv(texts["label"]),
    )
    row = lwc.pair_to_candidate(pairs[0], 0)
    assert row["transcript"] == "顾客问 salon chair —— relaxzen massage chair（标注：Exact）"
    assert row["source_video_label"] == lwc.SOURCE_VIDEO_LABEL
    assert (row["timecode_start"], row["timecode_end"]) == ("00:00:00", "00:00:39")
    later = lwc.pair_to_candidate(pairs[1], 2)
    assert (later["timecode_start"], later["timecode_end"]) == ("00:01:20", "00:01:59")


def test_wands_candidate_rows_timecodes_increase() -> None:
    """整批候选 timecode 严格递增、end>start、恒 8 字符（模型列宽 String(8)）。"""
    texts = _wands_texts()
    pairs = lwc.join_exact_pairs(
        lwc.parse_wands_tsv(texts["query"]),
        lwc.parse_wands_tsv(texts["product"]),
        lwc.parse_wands_tsv(texts["label"]),
    )
    rows = lwc.candidate_rows(pairs * 5)  # 放大批量看进位
    starts = [row["timecode_start"] for row in rows]
    assert starts == sorted(set(starts))
    assert all(row["timecode_end"] > row["timecode_start"] for row in rows)
    assert all(len(row["timecode_start"]) == 8 for row in rows)
    assert lwc.format_timecode(3600 + 120 + 3) == "01:02:03"


def test_wands_carrier_schema_is_furniture() -> None:
    schema = lwc._wands_schema()
    assert schema["材质"]["required"] is True


def test_wands_sampling_deterministic() -> None:
    pairs = [{"query_id": str(i), "query": f"q{i}", "product_id": str(i),
              "query_class": "", "product_name": f"p{i}", "product_class": ""} for i in range(20)]
    first = lwc.sample_pairs(pairs, 5, seed=42)
    second = lwc.sample_pairs(pairs, 5, seed=42)
    assert first == second and len(first) == 5
    assert lwc.sample_pairs(pairs, 10_000) == pairs  # n 超总量=全量


def test_wands_candidates_csv_roundtrip(tmp_path: Path) -> None:
    """候选预览 CSV（utf-8-sig）可原样读回，列序与 clip_candidates 形状对齐。"""
    texts = _wands_texts()
    pairs = lwc.sample_pairs(
        lwc.join_exact_pairs(
            lwc.parse_wands_tsv(texts["query"]),
            lwc.parse_wands_tsv(texts["product"]),
            lwc.parse_wands_tsv(texts["label"]),
        ),
        2,
        seed=7,
    )
    rows = lwc.candidate_rows(pairs)
    out = tmp_path / "candidates.csv"
    lwc.write_candidates_csv(rows, out)
    with open(out, encoding="utf-8-sig", newline="") as handle:
        read_back = list(csv.reader(handle))
    assert read_back[0] == ["timecode_start", "timecode_end", "transcript", "source_video_label"]
    assert read_back[1][2] == rows[0]["transcript"]


def test_off_dump_fixture_cleans_to_complete_rows_only() -> None:
    """dump 样本 5 行：2 条有条码+品名+可解析净含量，3 条清洗丢掉。不编造保质期。"""
    rows = loff.load_fixture_tsv(SAMPLES_DIR / "off_dump_sample.tsv", limit=10)
    assert [row["barcode"] for row in rows] == ["6921168500013", "3017620422003"]
    nongfu = rows[0]
    assert nongfu["name"] == "农夫山泉饮用天然水"
    assert nongfu["brands"] == "农夫山泉"
    assert nongfu["spec_schema"] == {"净含量": {"required": True}}
    assert nongfu["spec_values"] == {}
    text = loff.spec_text_from_row(nongfu)
    assert "净含量：550 ml" in text
    assert "保质期" not in text
    assert "见包装" not in text
    assert "6921168500013" in text
    from suite_api.services.machine_wash import extract_document_fields, extract_net_content

    assert extract_net_content("550 ml") == "550ml"
    extracted = extract_document_fields(text, ["净含量", "保质期"])
    assert extracted["净含量"]["value"] == "550ml"
    assert extracted["保质期"].get("abstained") is True


def test_review_cat_maps_to_product_category_or_none() -> None:
    assert lr.product_category_for_review_cat("手机") == "智能手机"
    assert lr.product_category_for_review_cat("衣服") is None
