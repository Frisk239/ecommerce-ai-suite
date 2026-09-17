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
刀 90 增：数码四类（QID 锚点/属性集常量、单类 SPARQL 形状、P571/P2048/P2049
换算、attrs 行级承载不冒充 spec_values、演示价单一真源）与评论类目白名单。
"""

import csv
import io
import json
import random
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
    """写出 CSV（utf-8-sig+JSON 串 spec/attrs 列）可原样读回，列序与模型字段对齐。"""
    rows = fwp.sparql_json_to_rows(_load_sparql_sample(), limit=5)
    out = tmp_path / "products.csv"
    fwp.write_products_csv(rows, out)
    with open(out, encoding="utf-8-sig", newline="") as handle:
        read_back = list(csv.reader(handle))
    assert tuple(read_back[0]) == fwp.CSV_COLUMNS
    assert len(read_back) == 6
    assert read_back[1][0] == rows[0]["name"]
    assert json.loads(read_back[1][2]) == rows[0]["spec_schema"]
    assert json.loads(read_back[1][4]) == rows[0]["attrs"]  # 六类目老查询无属性键={}
    assert int(read_back[1][5]) == rows[0]["stock"]


# ------------------------------------------------- 数码四类（第 90 刀）：查询/属性/行

# 数码 QID→类目（实测修正锚点，docs/research/real-store-data-sources.md §①）
DIGITAL_QID_TO_CATEGORY = {
    "Q250": "键盘",
    "Q7987": "鼠标",
    "Q5290": "显示器",
    "Q186819": "耳机",
}


def test_digital_categories_constant_matches_researched_qids() -> None:
    """四类 QID/中文名与调研定案一字不差（任务书原 QID 全部失准，防再错进常量）。"""
    assert {qid: name for qid, name, _ in fwp.DIGITAL_CATEGORIES} == DIGITAL_QID_TO_CATEGORY
    # 属性集：键盘/鼠标只抽制造商；显示器+高/宽；耳机+上市时间
    props = {name: set(props) for _, name, props in fwp.DIGITAL_CATEGORIES}
    assert props["键盘"] == {"P176"} and props["鼠标"] == {"P176"}
    assert props["显示器"] == {"P176", "P2048", "P2049"}
    assert props["耳机"] == {"P176", "P571"}


def test_digital_sparql_query_shape() -> None:
    """单类查询：BIND 类目名/子类展开/FILTER 自身/属性 OPTIONAL/制造商走 label 服务。"""
    for qid, zh_name, query_props in fwp.DIGITAL_CATEGORIES:
        query = fwp.build_digital_sparql_query(qid, zh_name, query_props, per_category=40)
        assert f"wd:{qid}" in query
        assert f'BIND("{zh_name}" AS ?category)' in query
        assert f"FILTER(?item != wd:{qid})" in query
        assert "LIMIT 40" in query
        assert query.count("UNION") == 0  # 单类逐查，不与大 UNION 混
        for prop in query_props:
            assert (
                f"OPTIONAL {{ ?item wdt:{prop} {fwp.DIGITAL_PROP_VARS[prop]} . }}" in query
            ), prop
    keyboard = fwp.build_digital_sparql_query("Q250", "键盘", ("P176",))
    assert "P2048" not in keyboard and "P2049" not in keyboard and "P571" not in keyboard
    monitor = fwp.build_digital_sparql_query("Q5290", "显示器", ("P176", "P2048", "P2049"))
    assert "?manufacturer rdfs:label ?manufacturerLabel." in monitor  # 品牌取标签
    assert fwp.build_digital_sparql_query("Q250", "键盘", (), per_category=7).count("LIMIT 7") == 1


def test_digital_attr_converters() -> None:
    """P571 取年、P2048/P2049 米转厘米整数；坏值/超合理域弃权（None 不编造）。"""
    assert fwp.inception_to_year("2016-01-01T00:00:00Z") == "2016"
    assert fwp.inception_to_year("1999") == "1999"
    assert fwp.inception_to_year("") is None
    assert fwp.inception_to_year("约 2010 年") is None
    assert fwp.metres_to_cm("0.36") == "36"
    assert fwp.metres_to_cm("1.555") == "156"  # 四舍五入保整数
    assert fwp.metres_to_cm("0.999") == "100"
    assert fwp.metres_to_cm("12") is None  # ≥10m 必非商品规格
    assert fwp.metres_to_cm("0") is None
    assert fwp.metres_to_cm("-1.2") is None
    assert fwp.metres_to_cm("abc") is None


def test_digital_attrs_from_binding() -> None:
    binding = {
        "category": {"value": "耳机"},
        "item": {"value": "http://www.wikidata.org/entity/Q1"},
        "itemLabel": {"value": "Sony WH-1000XM4"},
        "manufacturerLabel": {"value": "Sony"},
        "inception": {"value": "2016-08-01T00:00:00Z"},
    }
    assert fwp.digital_attrs_from_binding(binding) == {"品牌": "Sony", "上市年份": "2016"}
    # 六类目老查询无属性键 → {}；坏值键弃权不写
    legacy = {
        "category": {"value": "图书"},
        "item": {"value": "http://www.wikidata.org/entity/Q9"},
        "itemLabel": {"value": "某书"},
    }
    assert fwp.digital_attrs_from_binding(legacy) == {}
    partial = dict(binding, height={"value": "0.62"}, width={"value": "not-a-number"})
    assert fwp.digital_attrs_from_binding(partial) == {
        "品牌": "Sony",
        "上市年份": "2016",
        "高度": "62",  # 宽度非数字 → 不写键
    }


def test_digital_rows_carry_attrs_not_spec_values() -> None:
    """数码行：attrs 承载 Wikidata 属性（不冒充 spec_values 写回值）；类目模板接入。"""
    payload = {
        "results": {
            "bindings": [
                {
                    "category": {"value": "显示器"},
                    "item": {"value": "http://www.wikidata.org/entity/Q5"},
                    "itemLabel": {"value": "Dell UltraSharp U2720Q"},
                    "manufacturerLabel": {"value": "Dell"},
                    "height": {"value": "0.46"},
                    "width": {"value": "0.81"},
                },
                # 无 P176 的耳机：行仍保留，attrs 只带可解析键
                {
                    "category": {"value": "耳机"},
                    "item": {"value": "http://www.wikidata.org/entity/Q6"},
                    "itemLabel": {"value": "某蓝牙耳机"},
                },
            ]
        }
    }
    rows = fwp.sparql_json_to_rows(payload)
    assert rows[0]["attrs"] == {"品牌": "Dell", "高度": "46", "宽度": "81"}
    assert rows[0]["spec_values"] == {}
    assert rows[0]["spec_schema"]["品牌"]["required"] is True
    assert rows[0]["spec_schema"]["高度"]["required"] is False
    assert rows[1]["attrs"] == {}


def test_sparql_payload_cache_roundtrip_and_query_mismatch(tmp_path: Path) -> None:
    """逐查询缓存（第 90 刀）：命中返回 payload；查询串失配/坏文件 → None 重拉。"""
    cache = tmp_path / "wikidata_Q250.json"
    assert fwp.read_cached_payload(cache, "SELECT 1") is None  # 不存在
    fwp.write_cached_payload(cache, "SELECT 1", {"results": {"bindings": [1, 2]}})
    assert fwp.read_cached_payload(cache, "SELECT 1") == {"results": {"bindings": [1, 2]}}
    # per_category 等参数变了 → 查询串变 → 缓存失配，不拿旧结果冒充新查询
    assert fwp.read_cached_payload(cache, "SELECT 2") is None
    cache.write_text("not-json", encoding="utf-8")
    assert fwp.read_cached_payload(cache, "SELECT 1") is None  # 坏缓存当 miss


def test_demo_price_single_source_is_seed_table() -> None:
    """演示价单一真源=seed.CATEGORY_DEMO_PRICES：数码四类价格一致，未知类目 None。"""
    from suite_api.services.seed import CATEGORY_DEMO_PRICES

    assert fwp._demo_price_for("键盘") == CATEGORY_DEMO_PRICES["键盘"] == 39900
    assert fwp._demo_price_for("鼠标") == 19900
    assert fwp._demo_price_for("显示器") == 149900
    assert fwp._demo_price_for("耳机") == 59900
    assert fwp._demo_price_for("不存在的类目") is None


# ------------------------------------- 数码规格文档（第 90 刀）：候选/正文/标题

import publish_digital_specs as pds  # noqa: E402


def test_digital_spec_candidates_and_text(tmp_path: Path) -> None:
    """候选=耳机/显示器且有品牌；正文「品牌：X\n…\n类目：Y」；标题 OFF 形态。"""
    csv_path = tmp_path / "products.csv"
    # 用 csv 模块构造（attrs 是 JSON 串，内含逗号须整列引住——fetch 写出同款）
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fwp.CSV_COLUMNS)
        writer.writerow(
            [
                "WH-1000XM4",
                "耳机",
                "{}",
                "{}",
                json.dumps({"品牌": "Sony", "上市年份": "2016"}, ensure_ascii=False),
                5,
            ]
        )
        writer.writerow(
            ["某键盘", "键盘", "{}", "{}", json.dumps({"品牌": "Logitech"}), 7]
        )  # 键盘不在主力类目，不入候选
        writer.writerow(
            [
                "U2720Q",
                "显示器",
                "{}",
                "{}",
                json.dumps({"品牌": "Dell", "高度": "46", "宽度": "81"}, ensure_ascii=False),
                9,
            ]
        )
        writer.writerow(["无牌耳机", "耳机", "{}", "{}", "{}", 3])  # 无品牌：规格文档无从写起
    rows = pds.candidate_rows(csv_path)
    assert [row["name"] for row in rows] == ["WH-1000XM4", "U2720Q"]
    assert pds.spec_text("耳机", rows[0]["attrs"]) == "品牌：Sony\n上市年份：2016\n类目：耳机\n"
    assert pds.spec_text("显示器", rows[1]["attrs"]) == "品牌：Dell\n高度：46\n宽度：81\n类目：显示器\n"
    assert pds.spec_title("WH-1000XM4") == "WH-1000XM4 规格（Wikidata）"
    assert len(pds.spec_title("长" * 300)) == pds.TITLE_MAX  # assets.title String(200)


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


def test_filter_by_cats_whitelist() -> None:
    """--cats 类目白名单（第 90 刀）：逗号分隔剥空白；空/全空白=不过滤（默认不变）。"""
    rows = [("平板", "1", "a"), ("书籍", "0", "b"), ("计算机", "1", "c"), ("手机", "0", "d")]
    assert lr.filter_by_cats(rows, "平板,计算机,手机") == [
        ("平板", "1", "a"),
        ("计算机", "1", "c"),
        ("手机", "0", "d"),
    ]
    assert lr.filter_by_cats(rows, " 平板 , 手机 ") == [("平板", "1", "a"), ("手机", "0", "d")]
    assert lr.filter_by_cats(rows, ["计算机"]) == [("计算机", "1", "c")]
    assert lr.filter_by_cats(rows, "") is rows  # 默认行为不变：原列表原样
    assert lr.filter_by_cats(rows, None) is rows
    assert lr.filter_by_cats(rows, "  ") is rows  # 全空白视同未指定
    assert lr.filter_by_cats([], "平板") == []


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


# ----------------------------------- 审计 18 P2 清偿（第 93 刀顺手清 1–3）

# P2#1：confirm_fields 送 PATCH 的字段 = attrs ∩ 该类目 schema（不是 FIELD_ORDER
# 硬过滤）——fetch 属性集漂移时不会送 schema 外字段把该行打死。
def test_digital_spec_confirm_fields_is_attrs_intersect_schema() -> None:
    """耳机 schema = {品牌, 上市年份, 图片, 官网}：attrs 多出的「高度」（漂移）不送 PATCH。

    第 94a 刀（审计 18 归位）：四类各加 图片（P18）/官网（P856）两个可选字段位——
    attrs 带着它们时进正文与 PATCH（有图商品回填位），不带时行为不变。"""
    import suite_api.services.category_schema as cs

    assert set(cs.schema_for_category("耳机")) == {"品牌", "上市年份", "图片", "官网"}
    assert pds.spec_fields("耳机", {"品牌": "Sony", "上市年份": "2016", "高度": "18"}) == [
        "品牌",
        "上市年份",
    ]
    # 显示器 schema = {品牌, 高度, 宽度, 图片, 官网}：顺序按 schema（品牌恒首位），多余键剔除
    assert pds.spec_fields("显示器", {"品牌": "Dell", "高度": "46", "宽度": "81", "上市年份": "2020"}) == [
        "品牌",
        "高度",
        "宽度",
    ]
    # 正文与确认同一真源：正文里出现的字段必然可确认
    text = pds.spec_text("显示器", {"品牌": "Dell", "高度": "46", "宽度": "81", "上市年份": "2020"})
    assert text == "品牌：Dell\n高度：46\n宽度：81\n类目：显示器\n"
    assert "上市年份" not in text
    # 归位后的新字段位：attrs 带 图片/官网 时进正文（可选字段，不设必填闸）
    assert pds.spec_text(
        "耳机", {"品牌": "Sony", "图片": "Sony WH-1000XM3.jpg", "官网": "https://sony.com"}
    ) == "品牌：Sony\n图片：Sony WH-1000XM3.jpg\n官网：https://sony.com\n类目：耳机\n"


def test_digital_spec_confirm_fields_returns_ok_false_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH 失败不抛给整跑（单行失败跳过，幂等锚保证重跑自愈）——顺带钉 P2#1 的请求体。"""
    import urllib.error

    captured: dict[str, bytes] = {}

    class _Opener:
        def open(self, request, timeout=0):  # noqa: ANN001, ANN201 - 替身形态
            captured["body"] = request.data
            raise urllib.error.HTTPError(request.full_url, 422, "bad", {}, None)  # type: ignore[arg-type]

    assert pds.confirm_fields(_Opener(), "http://x", 1, 1, {"品牌": "Sony", "高度": "18"}, "耳机") is False
    assert json.loads(captured["body"]) == {"品牌": "Sony"}


# P2#2：candidate_rows 的坏输入给可行动报错（先跑 fetch），不裸 traceback、不静默跑完
def test_spec_candidates_reject_missing_columns_with_actionable_error(tmp_path: Path) -> None:
    csv_path = tmp_path / "old-products.csv"
    csv_path.write_text("name,category\n旧CSV,耳机\n", encoding="utf-8-sig")
    with pytest.raises(ValueError, match="先重跑"):
        pds.candidate_rows(csv_path)


def test_spec_candidates_reject_empty_and_unmatched_csv(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8-sig")  # 空文件：连表头都没有
    with pytest.raises(ValueError, match="缺列"):
        pds.candidate_rows(empty)

    only_header = tmp_path / "header-only.csv"
    only_header.write_text(",".join(fwp.CSV_COLUMNS) + "\n", encoding="utf-8-sig")
    with pytest.raises(ValueError, match="没有可发布的候选"):
        pds.candidate_rows(only_header)

    no_brand = tmp_path / "no-brand.csv"
    with open(no_brand, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fwp.CSV_COLUMNS)
        writer.writerow(["某耳机", "耳机", "{}", "{}", "{}", 1])
    with pytest.raises(ValueError, match="品牌"):
        pds.candidate_rows(no_brand)


def test_spec_candidates_reject_broken_attrs_json(tmp_path: Path) -> None:
    csv_path = tmp_path / "broken-attrs.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fwp.CSV_COLUMNS)
        writer.writerow(["某耳机", "耳机", "{}", "{}", "{not-json", 1])
    with pytest.raises(ValueError, match="attrs 不是 JSON"):
        pds.candidate_rows(csv_path)


# P2#3：数码行不再与 legacy 共享 limit——两组各带各的上限，共享 rng 与去重集合
def test_digital_rows_have_independent_limit() -> None:
    def _binding(name: str, category: str, index: int) -> dict:
        return {
            "category": {"value": category},
            "item": {"value": f"http://www.wikidata.org/entity/Q{index}"},
            "itemLabel": {"value": name},
        }

    legacy = [_binding(f"老商品{i}", "智能手机", i) for i in range(5)]
    digital = [_binding(f"数码{i}", "耳机", 100 + i) for i in range(5)]
    rng = random.Random(fwp.DEFAULT_SEED)
    seen: set[tuple[str, str]] = set()
    legacy_rows = fwp.rows_from_bindings(legacy, limit=3, rng=rng, seen=seen)
    digital_rows = fwp.rows_from_bindings(digital, limit=4, rng=rng, seen=seen)
    assert len(legacy_rows) == 3  # legacy 先吃满自己的上限
    assert len(digital_rows) == 4  # 数码行**不**被 legacy 的截断带走（旧口径这里会是 0）
    # 共享去重集合：跨组同名同类不重复
    again = fwp.rows_from_bindings([_binding("数码0", "耳机", 200)], limit=10, rng=rng, seen=seen)
    assert again == []


def test_digital_only_default_limit_constant_is_200() -> None:
    assert fwp.DEFAULT_DIGITAL_LIMIT == 200
    args = fwp.parse_args(["--digital-only"])
    assert args.digital_limit == 200 and args.limit == fwp.DEFAULT_LIMIT == 200


# ------------------------------------- OFF 错配订正（第 100 刀）：判据纯函数与表形状

import correct_off_mismatch as com  # noqa: E402


def test_off_mismatch_verdicts_on_demo_db_pairs() -> None:
    """判据对演示库实测形态的判定（阈值 4 的定标锚全部钉死）。

    - 真错配（token 交集空）：M&M white/Fitpiggy；
    - 相等词互证：xxx、Frog Fuel Power Protein/Frog Fuel；
    - 词干互证（≥4 公共子串）：Erdbeeren/BeerenBrüder 共享 beeren——README 主推
      演示问句，订正面不得波及；
    - 3 字符碰撞**不**互证：Graines de Chia/Nestle Carnation 共享 nes（把阈值
      立在 4 而非 3 的实证——3 会让这件完全无关的错配漏判）。
    """
    assert com.judge_mismatch("M&M white 规格（OFF）", "Fitpiggy") == com.VERDICT_MISMATCH
    assert com.judge_mismatch("xxx 规格（OFF）", "xxx") == com.VERDICT_MATCH
    assert com.judge_mismatch("Frog Fuel Power Protein 规格（OFF）", "Frog Fuel") == com.VERDICT_MATCH
    assert com.judge_mismatch("Erdbeeren 规格（OFF）", "BeerenBrüder") == com.VERDICT_MATCH
    assert com.judge_mismatch("Graines de Chia 规格（OFF）", "Nestle Carnation") == com.VERDICT_MISMATCH


def test_off_mismatch_empty_title_or_brand_is_unjudgeable() -> None:
    """边界：空标题（None/空串/纯后缀）与空品牌（None/空白）都不可判——不订正不猜。"""
    for title in (None, "", "   ", " 规格（OFF）"):
        assert com.judge_mismatch(title, "Fitpiggy") == com.VERDICT_NO_BRAND
    for brand in (None, "", "  "):
        assert com.judge_mismatch("M&M white 规格（OFF）", brand) == com.VERDICT_NO_BRAND


def test_off_subject_from_title_suffix_forms() -> None:
    assert com.subject_from_title("M&M white 规格（OFF）") == "M&M white"
    assert com.subject_from_title("X 规格(OFF)") == "X"  # 半角括号历史形态
    assert com.subject_from_title("钛钢保温杯 · 规格") == "钛钢保温杯 · 规格"  # 非 OFF 不剥
    assert com.subject_from_title(None) == ""
    assert com.subject_from_title("规格（OFF）") == ""  # 纯后缀=无商品名


def test_off_longest_common_substring() -> None:
    assert com.longest_common_substring("erdbeeren", "beerenbr") == len("beeren")
    assert com.longest_common_substring("nesquik", "nestl") == 3  # nes
    assert com.longest_common_substring("m", "fitpiggy") == 0
    assert com.longest_common_substring("", "abc") == 0
    assert com.longest_common_substring("abc", "abc") == 3


def test_off_corrected_title_keeps_suffix_shape() -> None:
    """订正标题恒带全角「 规格（OFF）」后缀（来源补写与标题锚依赖它），截到列宽。"""
    assert com.corrected_title("Fitpiggy") == "Fitpiggy 规格（OFF）"
    long = "b" * 300
    assert com.corrected_title(long) == (long + com.FULL_SUFFIX)[: com.TITLE_MAX]
    assert len(com.corrected_title(long)) == com.TITLE_MAX  # assets.title String(200)


def test_off_diagnose_row_and_table_shape() -> None:
    """诊断表契约：行六键（new_title 只在 mismatch），表含表头/汇总/mismatch 行的新标题。

    diagnose 吃 load_off_assets 产出的**品牌值**（「品牌：」块前缀已在读取层剥掉）。"""
    rows = com.diagnose(
        [
            {"id": 1, "title": "M&M white 规格（OFF）", "brand": "Fitpiggy"},
            {"id": 2, "title": "xxx 规格（OFF）", "brand": "xxx"},
            {"id": 3, "title": "Confiture 规格（OFF）", "brand": ""},
        ]
    )
    by_id = {row["asset_id"]: row for row in rows}
    assert set(by_id[1]) == {"asset_id", "title", "subject", "brand", "verdict", "new_title"}
    assert by_id[1]["verdict"] == com.VERDICT_MISMATCH
    assert by_id[1]["new_title"] == "Fitpiggy 规格（OFF）"
    assert by_id[2]["verdict"] == com.VERDICT_MATCH and by_id[2]["new_title"] is None
    assert by_id[3]["verdict"] == com.VERDICT_NO_BRAND and by_id[3]["new_title"] is None
    table = com.format_diagnosis_table(rows)
    assert "资产" in table.splitlines()[0] and "判定" in table.splitlines()[0]
    assert "共 3 份：mismatch=1, no_brand=1, match=1" in table
    assert "Fitpiggy 规格（OFF）" in table


def test_off_brand_from_chunk_field_line_only() -> None:
    assert com.brand_from_chunk("品牌：Fitpiggy") == "Fitpiggy"
    assert com.brand_from_chunk("品牌: 半角冒号") == "半角冒号"
    assert com.brand_from_chunk("净含量：250g") == ""
    assert com.brand_from_chunk("品牌：") == ""
