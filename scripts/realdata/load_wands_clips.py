"""WANDS 家具检索基准灌切片候选（多来源真实数据刀 II）：三 csv→join→灌 clip_candidates。

数据源与许可：WANDS（Wayfair，https://github.com/wayfair/WANDS ，MIT）——
480 query × 43K 商品 × 233K 三档相关性标注（Exact/Partial/Irrelevant）。
MIT 可入仓库演示，保留出处链接。

通道映射：走「切片拣选通道」——clip_candidates 是切片模块自有种子（ADR 0014，
无写端点），脚本直连 DB 幂等灌入（timecode+transcript 已存在跳过）。真视频
本体与切出是部署刀的事（0039）：登记字节=带时间码头的转写文本，本脚本只铺
「源录像上有这么一段值得拣选」的候选。ClipCandidate.product_id 不可空
（模型实测），故幂等造一个专属商品行「WANDS 家具（演示）」（category=家具）
承载全部候选；相关性档位没有独立列，拼进 transcript 尾注「（标注：Exact）」。

WANDS csv 实为 TSV（tab 分隔，表头 query_id/query/… 与 product_id/…），按
列名解析。网络：标准库 urllib（respect HTTPS_PROXY）。

用法（仓库根目录，先起栈：docker compose up -d）：
    uv run python scripts/realdata/load_wands_clips.py --n 30    # 下载→join→候选预览
    uv run python scripts/realdata/load_wands_clips.py --n 30 --load \
        --db postgresql://suite:suite@localhost:5433/suite       # 真灌 clip_candidates
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import random
import sys
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
OUT_DIR = SCRIPT_DIR / "out"

WANDS_BASE = "https://raw.githubusercontent.com/wayfair/WANDS/main/dataset"
DEFAULT_URLS = {
    "query": f"{WANDS_BASE}/query.csv",
    "product": f"{WANDS_BASE}/product.csv",
    "label": f"{WANDS_BASE}/label.csv",
}
DEFAULT_CACHE = {name: OUT_DIR / f"wands_{name}.csv" for name in DEFAULT_URLS}
DEFAULT_OUT_CSV = OUT_DIR / "wands_clip_candidates.csv"
USER_AGENT = "ecommerce-ai-suite-realdata/1.0 (demo data script; local repo)"

DEFAULT_N = 30
DEFAULT_SEED = 42
EXACT_LABEL = "Exact"
# 专属承载商品（clip_candidates.product_id 不可空）：幂等键 name+category
WANDS_PRODUCT_NAME = "WANDS 家具（演示）"
WANDS_PRODUCT_CATEGORY = "家具"
SOURCE_VIDEO_LABEL = "WANDS · wayfair 家具检索基准"


def _wands_schema() -> dict:
    try:
        from suite_api.services.category_schema import schema_for_category
    except ImportError:  # pragma: no cover
        return {}
    return schema_for_category(WANDS_PRODUCT_CATEGORY)
CLIP_SECONDS = 40  # 合成切片时长：timecode 自增步长（00:00:40 起）


# ---------------------------------------------------------------- 纯函数（可单测）


def parse_wands_tsv(text: str) -> list[dict[str, str]]:
    """TSV 文本 → 行 dict 列表（纯函数）：首行表头按列名定位（容 BOM）。

    列数不足的行跳过；字段剥首尾空白（WANDS 尾列常带换行填充）。
    """
    records = list(csv.reader(io.StringIO(text), delimiter="\t"))
    if not records:
        return []
    header = [cell.strip().lstrip("\ufeff") for cell in records[0]]
    rows: list[dict[str, str]] = []
    for record in records[1:]:
        if len(record) < len(header):
            continue
        rows.append({name: record[idx].strip() for idx, name in enumerate(header)})
    return rows


def join_exact_pairs(
    queries: list[dict[str, str]],
    products: list[dict[str, str]],
    labels: list[dict[str, str]],
) -> list[dict[str, str]]:
    """query ⋈ label(Exact) ⋈ product → 相关对（纯函数）。

    只留 label=Exact（Partial/Irrelevant 不入演示候选）；query/product 行
    按 id 建 dict；缺行或缺商品名跳过；(query_id, product_id) 去重（同对
    多标注行只留首个）。输出 {query_id, query, query_class, product_id,
    product_name, product_class}。
    """
    query_by_id = {row["query_id"]: row for row in queries if row.get("query_id")}
    product_by_id = {row["product_id"]: row for row in products if row.get("product_id")}
    pairs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label_row in labels:
        if label_row.get("label") != EXACT_LABEL:
            continue
        query = query_by_id.get(label_row.get("query_id", ""))
        product = product_by_id.get(label_row.get("product_id", ""))
        if query is None or product is None:
            continue
        key = (label_row["query_id"], label_row["product_id"])
        if key in seen:
            continue
        name = product.get("product_name", "").strip()
        question = query.get("query", "").strip()
        if not name or not question:
            continue
        seen.add(key)
        pairs.append(
            {
                "query_id": label_row["query_id"],
                "query": question,
                "query_class": query.get("query_class", ""),
                "product_id": label_row["product_id"],
                "product_name": name,
                "product_class": product.get("product_class", ""),
            }
        )
    return pairs


def format_timecode(seconds: int) -> str:
    """秒 → HH:MM:SS（8 字符，clip_candidates.timecode_* String(8) 同宽）。"""
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def pair_to_candidate(pair: dict[str, str], index: int) -> dict[str, str]:
    """相关对 + 序号 → 切片候选行（纯函数，合成形态）。

    transcript = `顾客问 {query} —— {product_name}（标注：Exact）`——相关性
    档位拼尾注（模型无独立列）；timecode 自 index 递增合成（源录像标签固定
    WANDS 基准，真时间码留真视频本体，部署刀）。
    """
    start = index * CLIP_SECONDS
    return {
        "timecode_start": format_timecode(start),
        "timecode_end": format_timecode(start + CLIP_SECONDS - 1),
        "transcript": f"顾客问 {pair['query']} —— {pair['product_name']}（标注：{EXACT_LABEL}）",
        "source_video_label": SOURCE_VIDEO_LABEL,
    }


def sample_pairs(
    pairs: list[dict[str, str]], n: int, seed: int = DEFAULT_SEED
) -> list[dict[str, str]]:
    """固定种子抽样 min(n, len(pairs)) 对（可复现；n 超总量返回全量）。"""
    if n >= len(pairs):
        return list(pairs)
    return random.Random(seed).sample(pairs, n)


def candidate_rows(pairs: list[dict[str, str]]) -> list[dict[str, str]]:
    """抽样对逐个转候选行（序号即合成 timecode 依据，顺序稳定可复现）。"""
    return [pair_to_candidate(pair, index) for index, pair in enumerate(pairs)]


def write_candidates_csv(rows: list[dict[str, str]], out_path: Path) -> None:
    """候选行 → CSV（utf-8-sig，Excel 可直开；列对齐 clip_candidates 形状）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    columns = ("timecode_start", "timecode_end", "transcript", "source_video_label")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[column] for column in columns])


# ---------------------------------------------------------------- 网络/IO（测试不吃）


def download_csvs(urls: dict[str, str], cache: dict[str, Path]) -> dict[str, str]:
    """三件 csv 逐个下载缓存（已缓存直读）。返回 {name: 文本}。"""
    texts: dict[str, str] = {}
    for name, url in urls.items():
        cache_path = cache[name]
        if cache_path.exists():
            print(f"使用缓存：{cache_path}")
            texts[name] = cache_path.read_text(encoding="utf-8")
            continue
        print(f"下载：{url}")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
        print(f"已缓存：{cache_path}（{len(data) / 1024 / 1024:.0f} MB）")
        texts[name] = data.decode("utf-8")
    return texts


def load_env_file(path: Path) -> int:
    """极简 .env 解析（与 load_abcd_dialogues.py 同一形态）。返回载入条数。"""
    if not path.is_file():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def load_clip_candidates(
    db_url: str, rows: list[dict[str, str]]
) -> tuple[int, int, int]:
    """直连 DB 幂等灌 clip_candidates。返回 (插入数, 跳过数, 承载商品 id)。

    幂等键 timecode_start+transcript（timecode 由序号合成，重跑同参数全跳过）；
    承载商品「WANDS 家具（演示）」按 name+category 幂等先行插入
    （product_id 不可空）。suite_api 从 uv workspace 解析（仓库根 uv run）。
    """
    from sqlalchemy import create_engine, select

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import ClipCandidate, Product

    # 与 API 同款 psycopg3 驱动归一化（db.py；裸 postgresql:// 会找不存在的 psycopg2）
    engine = create_engine(to_sqlalchemy_url(db_url))
    inserted = skipped = 0
    with engine.begin() as connection:
        product_id = connection.execute(
            select(Product.id).where(
                Product.name == WANDS_PRODUCT_NAME, Product.category == WANDS_PRODUCT_CATEGORY
            )
        ).scalar_one_or_none()
        if product_id is None:
            product_id = connection.execute(
                Product.__table__.insert()
                .values(
                    name=WANDS_PRODUCT_NAME,
                    category=WANDS_PRODUCT_CATEGORY,
                    spec_schema=_wands_schema(),
                    spec_values={},
                    stock=0,
                )
                .returning(Product.id)
            ).scalar_one()
        else:
            current = connection.execute(
                select(Product.spec_schema).where(Product.id == product_id)
            ).scalar_one()
            if not current:
                connection.execute(
                    Product.__table__.update()
                    .where(Product.id == product_id)
                    .values(spec_schema=_wands_schema())
                )
        for row in rows:
            exists = connection.execute(
                select(ClipCandidate.id).where(
                    ClipCandidate.timecode_start == row["timecode_start"],
                    ClipCandidate.transcript == row["transcript"],
                )
            ).first()
            if exists is not None:
                skipped += 1
                continue
            connection.execute(
                ClipCandidate.__table__.insert().values(
                    product_id=product_id,
                    status="pending",
                    timecode_start=row["timecode_start"],
                    timecode_end=row["timecode_end"],
                    transcript=row["transcript"],
                    source_video_label=row["source_video_label"],
                )
            )
            inserted += 1
    engine.dispose()
    return inserted, skipped, int(product_id)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WANDS 相关对→切片候选灌入（MIT）")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help=f"抽样相关对数（默认 {DEFAULT_N}）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="抽样随机种子（默认 42）")
    parser.add_argument("--cache-dir", type=Path, default=OUT_DIR, help="三件 csv 缓存目录")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_CSV, help="候选预览 CSV 路径")
    parser.add_argument(
        "--load", action="store_true", help="直连 DB 幂等灌 clip_candidates（附承载商品行）"
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL"),
        help="目标库 URL（默认 .env/DATABASE_URL）；宿主 compose 用 "
        "postgresql://suite:suite@localhost:5433/suite",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPO_ROOT / ".env",
        help="启动前载入的 .env（默认仓库根 .env，只取 DATABASE_URL；已设环境变量优先）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # .env 先于 argparse 载入（--db 默认值从环境来）
    load_env_file(REPO_ROOT / ".env")
    args = parse_args(argv)
    if args.n < 1:
        print("错误：--n 须 ≥ 1", file=sys.stderr)
        return 2
    if args.load and not args.db:
        print("错误：--load 需要 --db 或 DATABASE_URL（.env）", file=sys.stderr)
        return 2

    cache = {name: args.cache_dir / path.name for name, path in DEFAULT_CACHE.items()}
    texts = download_csvs(DEFAULT_URLS, cache)
    queries = parse_wands_tsv(texts["query"])
    products = parse_wands_tsv(texts["product"])
    labels = parse_wands_tsv(texts["label"])
    print(f"解析：query {len(queries)} 行，product {len(products)} 行，label {len(labels)} 行")

    pairs = join_exact_pairs(queries, products, labels)
    sampled = sample_pairs(pairs, args.n, args.seed)
    rows = candidate_rows(sampled)
    write_candidates_csv(rows, args.out)
    print(f"Exact 相关对 {len(pairs)}，抽样 {len(sampled)}（种子 {args.seed}），已写出：{args.out}")

    if not args.load:
        print("未指定 --load：到止（预览）。加 --load 直连灌 clip_candidates（幂等）。")
        return 0

    inserted, skipped, product_id = load_clip_candidates(args.db, rows)
    print(
        f"灌库完成：插入候选 {inserted}，跳过（timecode+transcript 已存在）{skipped}，"
        f"承载商品 id={product_id}（{WANDS_PRODUCT_NAME}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
