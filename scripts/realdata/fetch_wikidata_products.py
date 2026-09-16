"""Wikidata 商品种子拉取（多来源真实数据刀 I）：SPARQL 拉真商品 → CSV → 可选直连灌库。

数据源与许可：Wikidata（https://www.wikidata.org，CC0）——SPARQL 端点
query.wikidata.org 按类目 QID 抽样带中文标签的商品条目。只读公开数据，
不标识个人，CC0 无需额外授权；脚本即合规边界（详见同目录 README.md）。

通道映射：商品无写端点，本脚本走「种子通道」直连 DATABASE_URL 灌 products
（spec 裁决：数据工程定位，不动 conftest 测试种子）。

第 90 刀扩展：数码外设四类（键盘/鼠标/显示器/耳机，QID 经实测修正）单类逐查，
并抽规格属性（P176 制造商→品牌、P571 上市→上市年份、P2048/P2049 高/宽→
米转厘米）进行级 attrs——只随 CSV 走，不直写 spec_values（写回是治理发布语义）。

网络：标准库 urllib（零新增依赖），自动 respect HTTPS_PROXY/HTTP_PROXY 环境
变量（宿主代理 127.0.0.1:7890）；Wikidata 查询服务要求 User-Agent 头，且当前
激进限速约 1 请求/分钟——429 时按 Retry-After（无头则 65s）退避重试。

用法（仓库根目录）：
    uv run python scripts/realdata/fetch_wikidata_products.py            # 拉取→out/products.csv
    uv run python scripts/realdata/fetch_wikidata_products.py --load \
        --db postgresql://suite:suite@localhost:5433/suite               # 追加直连灌库（幂等）
    uv run python scripts/realdata/fetch_wikidata_products.py --digital-only --load \
        --db postgresql://suite:suite@localhost:5433/suite               # 只拉数码四类（第 90 刀）

行数上限（审计 18 P2#3 修）：``--limit`` 只管 legacy 六类、``--digital-limit`` 管
数码四类（默认各 200）——旧口径共享一个上限，数码行排在 legacy 之后，legacy 填满
即被静默截掉；现在两组各算各的，合并 CSV 最多 400 行。

逐查询 payload 缓存在 out/wikidata_*.json（查询串校验，参数变即重拉）——
WDQS 退避中进程被杀后重跑可续接，不重复消耗限速预算。

compose 内网络则用 --db postgresql://suite:suite@db:5432/suite。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR / "out" / "products.csv"

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
# WDQS 礼貌头（其 UA 政策要求带可识别客户端名）
USER_AGENT = "ecommerce-ai-suite-realdata/1.0 (demo data script; local repo)"
# 标签回退链：中文简体优先，无中文标签的条目回退英文（仍为真实商品名）
LABEL_LANGUAGES = "zh,zh-hans,zh-hant,en"

# 六个稳定类目（QID→中文名，2026-09-09 实测六类查询均非空；图书/笔记本满额 40，
# 其余类目中文标签较稀疏返回 6-17 条——上限 40 由 LIMIT 保证不超）
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Q22645", "智能手机"),
    ("Q3962", "笔记本电脑"),
    ("Q155972", "平板电脑"),
    ("Q8075", "电视机"),
    ("Q124441", "洗衣机"),
    ("Q571", "图书"),
)

# 数码外设四类（第 90 刀）：QID 经 2026-09-16 实测修正（任务书原 QID 全部失准，
# 详见 docs/research/real-store-data-sources.md §①），逐类带要抽的属性集。
# USB 电源适配器（Q64684632）实例池仅 1 件，不入取数。数码类**单类逐查**：
# 属性 OPTIONAL 三元组随类目不同，且 WDQS 限速激进（大 UNION 查询更易 429）。
DIGITAL_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Q250", "键盘", ("P176",)),
    ("Q7987", "鼠标", ("P176",)),
    ("Q5290", "显示器", ("P176", "P2048", "P2049")),
    ("Q186819", "耳机", ("P176", "P571")),
)

# Wikidata 属性 → 类目规格字段（category_schema 的键，单一真源在那边）：
# P176 制造商（取标签）→ 品牌（required 锚）；P571 上市时间 → 上市年份（取年）；
# P2048/P2049 高/宽（Wikidata 归一 SI 单位=米）→ 高度/宽度（厘米整数）。
DIGITAL_PROP_VARS: dict[str, str] = {
    "P176": "?manufacturer",
    "P571": "?inception",
    "P2048": "?height",
    "P2049": "?width",
}
DEFAULT_PER_CATEGORY = 40
DEFAULT_LIMIT = 200
# 数码行的独立上限（第 93 刀清审计 18 P2#3）：旧口径是 legacy 六类与数码四类共享
# 一个 limit——数码行排在 legacy 之后，legacy 一旦填满 200 行，数码行被静默截掉
# （语料增长时丢行且无提示）。现在两组各算各的上限（都有默认 200），合并后 CSV
# 可达 400 行：丢行不再无声。
DEFAULT_DIGITAL_LIMIT = 200
DEFAULT_SEED = 42
DEFAULT_TIMEOUT = 120
DEFAULT_TRIES = 3
# 无 Retry-After 头时的退避间隔（秒）：对齐 WDQS 激进限速 1 请求/分钟
RATE_LIMIT_WAIT = 65.0

# 输出 CSV 列（products 表灌入字段一一对应；spec_schema/spec_values/attrs 序列化为 JSON 串；
# attrs 是行级 Wikidata 属性抽取结果，只随 CSV 走供规格文档生成用，不灌 spec_values）
CSV_COLUMNS = ("name", "category", "spec_schema", "spec_values", "attrs", "stock")
# 与 Product 模型列宽对齐（String(200)/String(50)）
NAME_MAX = 200
CATEGORY_MAX = 50


def _schema_for(category: str) -> dict:
    """类目规格模板：与 suite_api.services.category_schema 同一份。

    SPARQL→行是纯函数、离线单测不连库；这里延迟 import，避免脚本 --help
    在无 workspace 时炸。测不到模板时退回 {}（测试环境有 suite_api）。
    """
    try:
        from suite_api.services.category_schema import schema_for_category
    except ImportError:  # pragma: no cover - uv workspace 外的防御
        return {}
    return schema_for_category(category)


def build_sparql_query(
    categories: tuple[tuple[str, str], ...] = CATEGORIES,
    per_category: int = DEFAULT_PER_CATEGORY,
) -> str:
    """构造按类目 UNION 采样的 SPARQL（每类 LIMIT per_category，BIND 常量类目名）。

    形状经实测（2026-09-09）：子查询取 (类目, 条目)，外层 wikibase:label 服务
    按 zh,zh-hans,zh-hant,en 回退解析标签；排除类目条目自身（如「智能手机」词条）。
    """
    blocks = []
    for qid, zh_name in categories:
        blocks.append(
            f"""  {{
    SELECT ?category ?item WHERE {{
      BIND("{zh_name}" AS ?category)
      ?item wdt:P31/wdt:P279* wd:{qid} .
      FILTER(?item != wd:{qid})
    }} LIMIT {per_category}
  }}"""
        )
    return (
        "PREFIX wdt: <http://www.wikidata.org/prop/direct/>\n"
        "PREFIX wd: <http://www.wikidata.org/entity/>\n"
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
        "PREFIX wikibase: <http://wikiba.se/ontology#>\n"
        "PREFIX bd: <http://www.bigdata.com/rdf#>\n"
        "SELECT ?category ?item ?itemLabel WHERE {\n"
        + "\n  UNION\n".join(blocks)
        + "\n"
        + '  SERVICE wikibase:label { bd:serviceParam wikibase:language "'
        + LABEL_LANGUAGES
        + '". ?item rdfs:label ?itemLabel. }\n'
        + "}"
    )


def build_digital_sparql_query(
    qid: str,
    zh_name: str,
    props: tuple[str, ...] = ("P176",),
    per_category: int = DEFAULT_PER_CATEGORY,
) -> str:
    """数码类目单类查询（第 90 刀）：一次请求只查一类。

    属性走 OPTIONAL（实测每件平均可用属性仅 1–2 个，强制 INNER JOIN 会把
    无属性条目全洗掉）；P176 制造商的标签随 label 服务同一条回退链解析
    （?manufacturer rdfs:label ?manufacturerLabel，zh 优先 en 兜底）。
    """
    optionals = "".join(
        f"  OPTIONAL {{ ?item wdt:{prop} {DIGITAL_PROP_VARS[prop]} . }}\n"
        for prop in props
    )
    manufacturer_label = (
        " ?manufacturer rdfs:label ?manufacturerLabel." if "P176" in props else ""
    )
    return (
        "PREFIX wdt: <http://www.wikidata.org/prop/direct/>\n"
        "PREFIX wd: <http://www.wikidata.org/entity/>\n"
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
        "PREFIX wikibase: <http://wikiba.se/ontology#>\n"
        "PREFIX bd: <http://www.bigdata.com/rdf#>\n"
        "SELECT ?category ?item ?itemLabel ?manufacturerLabel ?inception ?height ?width WHERE {\n"
        f'  BIND("{zh_name}" AS ?category)\n'
        f"  ?item wdt:P31/wdt:P279* wd:{qid} .\n"
        f"  FILTER(?item != wd:{qid})\n"
        f"{optionals}"
        '  SERVICE wikibase:label { bd:serviceParam wikibase:language "'
        + LABEL_LANGUAGES
        + f'". ?item rdfs:label ?itemLabel.{manufacturer_label} }}\n'
        "}"
        f" LIMIT {per_category}"
    )


def inception_to_year(value: str) -> str | None:
    """P571 日期串（如 ``2016-01-01T00:00:00Z``）→ 年份字符串；开头取不出 4 位年返回 None。"""
    match = re.match(r"^(\d{4})", value.strip())
    return match.group(1) if match else None


def metres_to_cm(value: str) -> str | None:
    """P2048/P2049 数量（米，Wikidata 归一 SI）→ 厘米整数字符串（四舍五入）。

    非数字或超出商品规格合理域（0 < m < 10；≥10 米必非键盘/耳机尺寸）→ None
    （弃权不编造，0009 同口径）。
    """
    try:
        metres = float(value.strip())
    except ValueError:
        return None
    if not 0 < metres < 10:
        return None
    return str(round(metres * 100))


def digital_attrs_from_binding(binding: dict[str, Any]) -> dict[str, str]:
    """数码行属性绑定 → {规格字段: 值}（纯函数；六类目老查询无这些键 → {}）。

    P176 取制造商标签→品牌；P571→上市年份（取年）；P2048/P2049→高度/宽度
    （米转厘米整数）。取不到/不可解析的字段不写键——行级弃权。
    """
    attrs: dict[str, str] = {}
    manufacturer = _binding_value(binding, "manufacturerLabel")
    if manufacturer:
        attrs["品牌"] = manufacturer
    year = inception_to_year(_binding_value(binding, "inception"))
    if year:
        attrs["上市年份"] = year
    for key, field in (("height", "高度"), ("width", "宽度")):
        cm = metres_to_cm(_binding_value(binding, key))
        if cm:
            attrs[field] = cm
    return attrs


def _binding_value(binding: dict[str, Any], key: str) -> str:
    cell = binding.get(key) or {}
    return str(cell.get("value", "")).strip() if isinstance(cell, dict) else ""


def rows_from_bindings(
    bindings: list[dict[str, Any]],
    *,
    limit: int,
    rng: random.Random,
    seen: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """绑定列表 → 商品行（**共享上限计算的核心**：rng 序列与去重集合由调用方持有）。

    第 93 刀拆出（审计 18 P2#3）：legacy 组与数码组各带各的 limit，但共用同一个
    rng（stock 序列可复现，与拆分前逐位一致）与 (name, category) 去重集合。
    """
    rows: list[dict[str, Any]] = []
    for binding in bindings:
        label = _binding_value(binding, "itemLabel")
        category = _binding_value(binding, "category")
        item_qid = _binding_value(binding, "item").rsplit("/", 1)[-1]
        if not label or not category:
            continue
        if label == item_qid:  # 无任何可用标签：label 服务回填条目 QID 串
            continue
        name = label[:NAME_MAX]
        cat = category[:CATEGORY_MAX]
        if (name, cat) in seen:
            continue
        seen.add((name, cat))
        row = {
            "name": name,
            "category": cat,
            "spec_schema": _schema_for(cat),
            "spec_values": {},
            "attrs": digital_attrs_from_binding(binding),
            "stock": rng.randrange(0, 100),
        }
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def sparql_json_to_rows(
    payload: dict[str, Any],
    limit: int = DEFAULT_LIMIT,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    """SPARQL 结果 JSON → 商品行列表（纯函数：name/category/spec/attrs/stock）。

    过滤：缺标签或缺类目跳过；label 服务无标签兜底返回 QID 串（与条目 QID 相同）
    不是可用商品名，跳过。(name, category) 去重；name/category 截断到模型列宽；
    attrs = 数码属性抽取（行级，随 CSV 走不灌 spec_values——写回是治理发布语义）；
    stock 用固定种子 rng 取 0-99（演示店铺语境的 mock 库存，可复现）。

    单 payload 形态保留给测试与单组调用；main 走 ``rows_from_bindings`` 分组
    各带上限（P2#3）。
    """
    return rows_from_bindings(
        (payload.get("results") or {}).get("bindings") or [],
        limit=limit,
        rng=random.Random(seed),
        seen=set(),
    )


def fetch_sparql(
    query: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_tries: int = DEFAULT_TRIES,
    rate_limit_wait: float = RATE_LIMIT_WAIT,
) -> dict[str, Any]:
    """执行 SPARQL 拉取并解析 JSON。429/网络错误退避重试，全败抛 RuntimeError。

    urllib 默认读 HTTPS_PROXY/HTTP_PROXY 环境变量走代理（宿主 127.0.0.1:7890）。
    """
    url = SPARQL_ENDPOINT + "?" + urllib.parse.urlencode({"format": "json", "query": query})
    last_error: Exception | None = None
    for attempt in range(1, max_tries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/sparql-results+json",
            },
        )
        wait = rate_limit_wait
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)  # type: ignore[no-any-return]
        except urllib.error.HTTPError as exc:
            last_error = exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and retry_after.isdigit():
                wait = float(retry_after)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < max_tries:
            print(
                f"  第 {attempt}/{max_tries} 次请求失败：{last_error}；{wait:.0f}s 后重试",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise RuntimeError(f"SPARQL 请求 {max_tries} 次均失败：{last_error}") from last_error


# WDQS 限速激进（429 常带 1000s 级 Retry-After）：一跑 5 个查询动辄一小时+，
# 进程中途被杀则全部进度蒸发（结果只在内存）。逐查询 payload 落盘缓存
# （load_reviews 缓存 zip / load_openfoodfacts 缓存 dump 同款纪律）——缓存里
# 记下查询串，参数变了（如 per_category）即失配重拉，不拿旧结果冒充新查询。
DEFAULT_CACHE_DIR = SCRIPT_DIR / "out"


def read_cached_payload(cache_path: Path, query: str) -> dict[str, Any] | None:
    """缓存命中且查询串一致 → payload；否则 None（调用方走网络）。"""
    if not cache_path.exists():
        return None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(cached, dict) or cached.get("query") != query:
        return None
    return cached.get("payload")  # type: ignore[no-any-return]


def write_cached_payload(cache_path: Path, query: str, payload: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"query": query, "payload": payload}, ensure_ascii=False), encoding="utf-8"
    )


def fetch_sparql_cached(
    cache_path: Path,
    query: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_tries: int = DEFAULT_TRIES,
    rate_limit_wait: float = RATE_LIMIT_WAIT,
) -> dict[str, Any]:
    """fetch_sparql 的断点续接形态：先读缓存，未命中才走网络并落盘。"""
    cached = read_cached_payload(cache_path, query)
    if cached is not None:
        print(f"使用缓存：{cache_path.name}")
        return cached
    payload = fetch_sparql(query, timeout, max_tries, rate_limit_wait)
    write_cached_payload(cache_path, query, payload)
    return payload


def write_products_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    """商品行 → CSV（utf-8-sig，Excel 可直开；spec/attrs 列序列化为 JSON 串）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row["name"],
                    row["category"],
                    json.dumps(row["spec_schema"], ensure_ascii=False),
                    json.dumps(row["spec_values"], ensure_ascii=False),
                    json.dumps(row["attrs"], ensure_ascii=False),
                    row["stock"],
                ]
            )


def _demo_price_for(category: str) -> int | None:
    """类目基准演示价（分）：seed.CATEGORY_DEMO_PRICES 单一真源（延迟 import 同
    _schema_for——mock 演示价非真实售价；None=该类目无基准价，不动）。"""
    try:
        from suite_api.services.seed import CATEGORY_DEMO_PRICES
    except ImportError:  # pragma: no cover - uv workspace 外的防御
        return None
    return CATEGORY_DEMO_PRICES.get(category)


def load_products(db_url: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """直连 DB 幂等灌 products：name+category 已存在跳过。返回 (插入数, 跳过数)。

    suite_api 从 uv workspace 解析（仓库根 uv run）；JSONB 列直接给 dict。
    演示价（第 90 刀）：新行按类目基准价写入、存量行仅 NULL 回填——与迁移 0026
    /第 41 刀同口径（**只写 NULL，不覆盖手改价**）；迁移是一次性快照，数码四类
    的价在导入通道这生效。
    """
    from sqlalchemy import create_engine, select

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import Product

    # 与 API 同款 psycopg3 驱动归一化（db.py；裸 postgresql:// 会找不存在的 psycopg2）
    engine = create_engine(to_sqlalchemy_url(db_url))
    inserted = skipped = 0
    with engine.begin() as connection:
        for row in rows:
            demo_price = _demo_price_for(row["category"])
            exists = connection.execute(
                select(Product.id).where(
                    Product.name == row["name"], Product.category == row["category"]
                )
            ).first()
            if exists is not None:
                skipped += 1
                current = connection.execute(
                    select(Product.spec_schema).where(Product.id == exists[0])
                ).scalar_one()
                if not current and row["spec_schema"]:
                    connection.execute(
                        Product.__table__.update()
                        .where(Product.id == exists[0])
                        .values(spec_schema=row["spec_schema"])
                    )
                price = connection.execute(
                    select(Product.price_cents).where(Product.id == exists[0])
                ).scalar_one()
                if price is None and demo_price is not None:
                    connection.execute(
                        Product.__table__.update()
                        .where(Product.id == exists[0])
                        .values(price_cents=demo_price)
                    )
                continue
            connection.execute(
                Product.__table__.insert().values(
                    name=row["name"],
                    category=row["category"],
                    spec_schema=row["spec_schema"],
                    spec_values=row["spec_values"],
                    stock=row["stock"],
                    price_cents=demo_price,
                    # 第 50 刀：来源随导入一起写（否则产品面的「开放数据集」只是
                    # 迁移回填的一次性快照，重置演示库/新环境就复现不出来）
                    source_kind="wikidata",
                )
            )
            inserted += 1
    engine.dispose()
    return inserted, skipped


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wikidata 商品种子拉取（CC0）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="legacy 六类行数上限（默认 200）")
    parser.add_argument(
        "--digital-limit",
        type=int,
        default=DEFAULT_DIGITAL_LIMIT,
        help="数码四类行数上限（默认 200；与 --limit 独立——P2#3：不再共享一个上限）",
    )
    parser.add_argument(
        "--per-category",
        type=int,
        default=DEFAULT_PER_CATEGORY,
        help="每类目采样上限（默认 40）",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="stock 随机种子（默认 42）")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="单请求超时秒（默认 120）"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出 CSV 路径")
    parser.add_argument(
        "--digital-only",
        action="store_true",
        help="只拉数码四类（第 90 刀；六类目已在库时省一次大 UNION 查询的限速等待）",
    )
    parser.add_argument(
        "--load", action="store_true", help="拉取后直连 DB 灌 products（幂等）"
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL"),
        help="目标库 URL（默认取 DATABASE_URL 环境变量）；"
        "宿主 compose 用 postgresql://suite:suite@localhost:5433/suite，"
        "容器内用 postgresql://suite:suite@db:5432/suite",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.load and not args.db:
        print("错误：--load 需要 --db 或 DATABASE_URL 环境变量", file=sys.stderr)
        return 2

    legacy_names = "0 类目（--digital-only）" if args.digital_only else f"{len(CATEGORIES)} 类目（UNION 一次）"
    print(
        f"SPARQL 拉取：{legacy_names} + {len(DIGITAL_CATEGORIES)} 数码类目（单类逐查，逐查询缓存）……"
    )
    legacy_bindings: list[dict[str, Any]] = []
    digital_bindings: list[dict[str, Any]] = []
    if not args.digital_only:
        payload = fetch_sparql_cached(
            DEFAULT_CACHE_DIR / "wikidata_legacy.json",
            build_sparql_query(CATEGORIES, args.per_category),
            timeout=args.timeout,
        )
        legacy_bindings.extend((payload.get("results") or {}).get("bindings") or [])
    for qid, zh_name, props in DIGITAL_CATEGORIES:
        print(f"  数码类目 {zh_name}（{qid}，属性 {'/'.join(props)}）单类查询 ……")
        payload = fetch_sparql_cached(
            DEFAULT_CACHE_DIR / f"wikidata_{qid}.json",
            build_digital_sparql_query(qid, zh_name, props, args.per_category),
            timeout=args.timeout,
        )
        digital_bindings.extend((payload.get("results") or {}).get("bindings") or [])
    # 分组转换（P2#3）：legacy 与数码各带各的上限，共享单 rng 序列 + 跨组去重
    # （与旧单次合并逐位一致：迭代顺序仍是 legacy 组在前、数码组在后）
    rng = random.Random(args.seed)
    seen: set[tuple[str, str]] = set()
    legacy_rows = rows_from_bindings(
        legacy_bindings, limit=args.limit, rng=rng, seen=seen
    )
    digital_rows = rows_from_bindings(
        digital_bindings, limit=args.digital_limit, rng=rng, seen=seen
    )
    rows = legacy_rows + digital_rows
    per_category: dict[str, int] = {}
    for row in rows:
        per_category[row["category"]] = per_category.get(row["category"], 0) + 1
    for _qid, zh_name in CATEGORIES:
        if not args.digital_only:
            print(f"  {zh_name}: {per_category.get(zh_name, 0)} 条")
    for _qid, zh_name, _props in DIGITAL_CATEGORIES:
        print(f"  {zh_name}: {per_category.get(zh_name, 0)} 条")
    print(
        f"共 {len(rows)} 行（去重后；legacy 上限 {args.limit} -> {len(legacy_rows)} 行，"
        f"数码上限 {args.digital_limit} -> {len(digital_rows)} 行）"
    )

    write_products_csv(rows, args.out)
    print(f"已写出：{args.out}")

    if args.load:
        inserted, skipped = load_products(args.db, rows)
        print(f"灌库完成：插入 {inserted}，跳过（name+category 已存在）{skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
