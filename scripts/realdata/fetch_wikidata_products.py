"""Wikidata 商品种子拉取（多来源真实数据刀 I）：SPARQL 拉真商品 → CSV → 可选直连灌库。

数据源与许可：Wikidata（https://www.wikidata.org，CC0）——SPARQL 端点
query.wikidata.org 按类目 QID 抽样带中文标签的商品条目。只读公开数据，
不标识个人，CC0 无需额外授权；脚本即合规边界（详见同目录 README.md）。

通道映射：商品无写端点，本脚本走「种子通道」直连 DATABASE_URL 灌 products
（spec 裁决：数据工程定位，不动 conftest 测试种子）。

网络：标准库 urllib（零新增依赖），自动 respect HTTPS_PROXY/HTTP_PROXY 环境
变量（宿主代理 127.0.0.1:7890）；Wikidata 查询服务要求 User-Agent 头，且当前
激进限速约 1 请求/分钟——429 时按 Retry-After（无头则 65s）退避重试。

用法（仓库根目录）：
    uv run python scripts/realdata/fetch_wikidata_products.py            # 拉取→out/products.csv
    uv run python scripts/realdata/fetch_wikidata_products.py --load \
        --db postgresql://suite:suite@localhost:5433/suite               # 追加直连灌库（幂等）

compose 内网络则用 --db postgresql://suite:suite@db:5432/suite。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
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
DEFAULT_PER_CATEGORY = 40
DEFAULT_LIMIT = 200
DEFAULT_SEED = 42
DEFAULT_TIMEOUT = 120
DEFAULT_TRIES = 3
# 无 Retry-After 头时的退避间隔（秒）：对齐 WDQS 激进限速 1 请求/分钟
RATE_LIMIT_WAIT = 65.0

# 输出 CSV 列（products 表灌入字段一一对应；spec_schema/spec_values 序列化为 JSON 串）
CSV_COLUMNS = ("name", "category", "spec_schema", "spec_values", "stock")
# 与 Product 模型列宽对齐（String(200)/String(50)）
NAME_MAX = 200
CATEGORY_MAX = 50


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


def _binding_value(binding: dict[str, Any], key: str) -> str:
    cell = binding.get(key) or {}
    return str(cell.get("value", "")).strip() if isinstance(cell, dict) else ""


def sparql_json_to_rows(
    payload: dict[str, Any],
    limit: int = DEFAULT_LIMIT,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    """SPARQL 结果 JSON → 商品行列表（纯函数：name/category/spec/stock）。

    过滤：缺标签或缺类目跳过；label 服务无标签兜底返回 QID 串（与条目 QID 相同）
    不是可用商品名，跳过。(name, category) 去重；name/category 截断到模型列宽；
    stock 用固定种子 rng 取 0-99（演示店铺语境的 mock 库存，可复现）。
    """
    rng = random.Random(seed)
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    bindings = (payload.get("results") or {}).get("bindings") or []
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
        rows.append(
            {
                "name": name,
                "category": cat,
                "spec_schema": {},
                "spec_values": {},
                "stock": rng.randrange(0, 100),
            }
        )
        if len(rows) >= limit:
            break
    return rows


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


def write_products_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    """商品行 → CSV（utf-8-sig，Excel 可直开；spec 两列序列化为 JSON 串）。"""
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
                    row["stock"],
                ]
            )


def load_products(db_url: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """直连 DB 幂等灌 products：name+category 已存在跳过。返回 (插入数, 跳过数)。

    suite_api 从 uv workspace 解析（仓库根 uv run）；JSONB 列直接给 dict。
    """
    from sqlalchemy import create_engine, select

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import Product

    # 与 API 同款 psycopg3 驱动归一化（db.py；裸 postgresql:// 会找不存在的 psycopg2）
    engine = create_engine(to_sqlalchemy_url(db_url))
    inserted = skipped = 0
    with engine.begin() as connection:
        for row in rows:
            exists = connection.execute(
                select(Product.id).where(
                    Product.name == row["name"], Product.category == row["category"]
                )
            ).first()
            if exists is not None:
                skipped += 1
                continue
            connection.execute(
                Product.__table__.insert().values(
                    name=row["name"],
                    category=row["category"],
                    spec_schema=row["spec_schema"],
                    spec_values=row["spec_values"],
                    stock=row["stock"],
                )
            )
            inserted += 1
    engine.dispose()
    return inserted, skipped


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wikidata 商品种子拉取（CC0）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="总行数上限（默认 200）")
    parser.add_argument(
        "--per-category",
        type=int,
        default=DEFAULT_PER_CATEGORY,
        help="每类目采样上限（默认 40）",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="stock 随机种子（默认 42）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出 CSV 路径")
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

    print(f"SPARQL 拉取：{len(CATEGORIES)} 类目 × 上限 {args.per_category} ……")
    payload = fetch_sparql(build_sparql_query(CATEGORIES, args.per_category))
    rows = sparql_json_to_rows(payload, limit=args.limit, seed=args.seed)
    per_category: dict[str, int] = {}
    for row in rows:
        per_category[row["category"]] = per_category.get(row["category"], 0) + 1
    for _qid, zh_name in CATEGORIES:
        print(f"  {zh_name}: {per_category.get(zh_name, 0)} 条")
    print(f"共 {len(rows)} 行（去重后，上限 {args.limit}）")

    write_products_csv(rows, args.out)
    print(f"已写出：{args.out}")

    if args.load:
        inserted, skipped = load_products(args.db, rows)
        print(f"灌库完成：插入 {inserted}，跳过（name+category 已存在）{skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
