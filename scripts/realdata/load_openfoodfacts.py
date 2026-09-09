"""Open Food Facts 公开 dump → 清洗 → 中台商品 + 规格文档（第 33 刀）。

数据源：夜更 TSV dump（tab 分隔，gzip）
https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz
字段说明：https://world.openfoodfacts.org/data/data-fields.txt
许可：数据库 ODbL；演示须署名。不标识个人。

流水线（对齐 PIM：feed → 清洗 → 质量闸 → 发布）：
  dump 行必须有 code + product_name + quantity（能被净含量正则抽出）
  → 丢掉空名/无条码/无净含量
  → spec_schema 只含 dump 里有的字段（有 quantity 才要求净含量；**不编造保质期**）
  → 规格字节 = dump 原文拼出的标签文本
  → --load 灌 products（spec_values 空，0010）
  → --register 走既有 register + 确认机洗净含量 + 发布写回

网络：标准库 urllib + gzip，流式解压，凑满 --n 条即停，不把 9GB 落盘。
抽样结果缓存 out/off_clean.tsv（小文件，复跑可 --fixture）。

用法（仓库根）：
    uv run python scripts/realdata/load_openfoodfacts.py --n 80
    uv run python scripts/realdata/load_openfoodfacts.py --n 80 --load --register --publish \\
        --db postgresql://suite:suite@localhost:5433/suite --api http://localhost:8000
    uv run python scripts/realdata/load_openfoodfacts.py --fixture scripts/realdata/samples/off_dump_sample.tsv
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import http.cookiejar
import io
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, TextIO

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE = SCRIPT_DIR / "out" / "off_clean.tsv"
DUMP_URL = "https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz"
USER_AGENT = "ecommerce-ai-suite-realdata/1.0 (ODbL reuse; local demo catalog ingest)"
DEFAULT_N = 80
FOOD_CATEGORY = "食品"
NAME_MAX = 200
DUMP_FIELDS = ("code", "product_name", "brands", "quantity", "ingredients_text", "countries_en")


def _net_content(quantity: str) -> str | None:
    """与机洗同一条净含量正则；抽不到则这行不是可用规格源。"""
    try:
        from suite_api.services.machine_wash import extract_net_content
    except ImportError:  # pragma: no cover
        import re

        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(ml|毫升|kg|千克|l|升|g|克)", quantity, re.IGNORECASE
        )
        return f"{match.group(1)}{match.group(2)}" if match else None
    return extract_net_content(quantity)


def clean_dump_row(raw: dict[str, str]) -> dict[str, Any] | None:
    """清洗一条 dump 行。缺条码/品名/可解析净含量 → None。不编造保质期。"""
    code = html.unescape((raw.get("code") or "").strip())
    name = html.unescape((raw.get("product_name") or raw.get("product_name_en") or "").strip())
    quantity = html.unescape((raw.get("quantity") or "").strip())
    brands = html.unescape((raw.get("brands") or "").strip())
    ingredients = html.unescape((raw.get("ingredients_text") or "").strip())
    countries = html.unescape((raw.get("countries_en") or "").strip())
    if not code.isdigit() or not name or not quantity:
        return None
    if _net_content(quantity) is None:
        return None
    return {
        "barcode": code,
        "name": name[:NAME_MAX],
        "category": FOOD_CATEGORY,
        "brands": brands,
        "quantity": quantity,
        "ingredients": ingredients,
        "countries": countries,
        # 评审(33) 处置：食品类共享模板（category_schema）还要求保质期 required，
        # 但 OFF dump 无保质期字段、ADR 0009 禁编造——这里是有意收窄为「只要求
        # 净含量」，非绕过模板；保质期留给操作者按包装补（弃权口径）。
        "spec_schema": {"净含量": {"required": True}},
        "spec_values": {},
        "stock": 12,
    }


def spec_text_from_row(row: dict[str, Any]) -> str:
    """只用 dump 里出现的字段拼规格字节。没有的键不写（0009）。"""
    lines = ["【Open Food Facts】", f"条码：{row['barcode']}"]
    if row.get("brands"):
        lines.append(f"品牌：{row['brands']}")
    lines.append(f"净含量：{row['quantity']}")
    if row.get("ingredients"):
        lines.append(f"配料：{row['ingredients'][:800]}")
    if row.get("countries"):
        lines.append(f"销售国家：{row['countries']}")
    lines.append(f"来源：https://world.openfoodfacts.org/product/{row['barcode']}")
    lines.append("许可：Open Food Facts 数据库 ODbL")
    return "\n".join(lines) + "\n"


def iter_dump_rows(text_stream: TextIO) -> Iterator[dict[str, str]]:
    """OFF dump 是 UTF-8 tab 分隔 CSV。"""
    reader = csv.DictReader(text_stream, delimiter="\t")
    for raw in reader:
        if not raw:
            continue
        yield {key: (value or "") for key, value in raw.items() if key}


def take_clean_rows(raw_rows: Iterable[dict[str, str]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in raw_rows:
        row = clean_dump_row(raw)
        if row is None or row["barcode"] in seen:
            continue
        seen.add(row["barcode"])
        out.append(row)
        if len(out) >= limit:
            break
    return out


def load_fixture_tsv(path: Path, limit: int) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    return take_clean_rows(iter_dump_rows(io.StringIO(text)), limit)


def stream_dump(url: str, limit: int, timeout: int = 300) -> list[dict[str, Any]]:
    """流式解压 gzip dump，凑满 limit 条干净行即停。"""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with gzip.GzipFile(fileobj=response) as unzipped:
            text_stream = io.TextIOWrapper(unzipped, encoding="utf-8", errors="replace")
            return take_clean_rows(iter_dump_rows(text_stream), limit)


def write_clean_tsv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(DUMP_FIELDS), delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "code": row["barcode"],
                    "product_name": row["name"],
                    "brands": row.get("brands") or "",
                    "quantity": row["quantity"],
                    "ingredients_text": row.get("ingredients") or "",
                    "countries_en": row.get("countries") or "",
                }
            )


def load_products(db_url: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    from sqlalchemy import create_engine, select

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import Product

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
                current = connection.execute(
                    select(Product.spec_schema).where(Product.id == exists[0])
                ).scalar_one()
                if not current:
                    connection.execute(
                        Product.__table__.update()
                        .where(Product.id == exists[0])
                        .values(spec_schema=row["spec_schema"])
                    )
                continue
            connection.execute(
                Product.__table__.insert().values(
                    name=row["name"],
                    category=row["category"],
                    spec_schema=row["spec_schema"],
                    spec_values={},
                    stock=row["stock"],
                )
            )
            inserted += 1
    engine.dispose()
    return inserted, skipped


def _login(base_url: str, username: str, password: str) -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body = json.dumps({"username": username, "password": password}).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/auth/login",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with opener.open(request, timeout=30) as response:
        response.read()
    return opener


def _multipart(
    fields: dict[str, str], filename: str, payload: bytes
) -> tuple[bytes, str]:
    boundary = "----offIngest7d9f2a"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + payload
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def register_and_publish(
    base_url: str,
    username: str,
    password: str,
    db_url: str,
    rows: list[dict[str, Any]],
    publish: bool,
) -> tuple[int, int]:
    """登记规格文档；确认机洗抽出的净含量；可选发布。返回 (登记数, 发布成功数)。"""
    from sqlalchemy import create_engine, select

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import Product

    opener = _login(base_url, username, password)
    engine = create_engine(to_sqlalchemy_url(db_url))
    registered = published = 0
    with engine.connect() as connection:
        for row in rows:
            product_id = connection.execute(
                select(Product.id).where(
                    Product.name == row["name"], Product.category == row["category"]
                )
            ).scalar_one_or_none()
            if product_id is None:
                continue
            body, boundary = _multipart(
                {"productId": str(product_id), "title": f"{row['name']} 规格（OFF）"[:200]},
                "off-spec.txt",
                spec_text_from_row(row).encode("utf-8"),
            )
            request = urllib.request.Request(
                base_url.rstrip("/") + "/api/assets/register",
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            try:
                with opener.open(request, timeout=60) as response:
                    asset = json.load(response)
            except urllib.error.HTTPError as exc:
                print(f"  登记失败 {row['barcode']}: HTTP {exc.code}", file=sys.stderr)
                continue
            registered += 1
            asset_id = int(asset["id"])
            versions = asset.get("versions") or []
            # register 每次新建 v1：本脚本语境刚登记的资产恒单版本；版本号取自
            # 登记响应而非硬编码 /versions/1——形态异常时显式报错跳过，不猜。
            if len(versions) != 1 or versions[0].get("version_no") != 1:
                print(
                    f"  资产 {asset_id} 登记响应非单 v1（{len(versions)} 版），跳过净含量确认",
                    file=sys.stderr,
                )
                continue
            extracted = versions[0].get("extracted_fields") or {}
            net = (extracted.get("净含量") or {}).get("value")
            if not net:
                continue
            version_no = versions[0]["version_no"]
            patch = urllib.request.Request(
                base_url.rstrip("/") + f"/api/assets/{asset_id}/versions/{version_no}/fields",
                data=json.dumps({"净含量": net}).encode(),
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                method="PATCH",
            )
            try:
                with opener.open(patch, timeout=30) as response:
                    response.read()
            except urllib.error.HTTPError:
                continue
            if not publish:
                continue
            pub = urllib.request.Request(
                base_url.rstrip("/") + f"/api/assets/{asset_id}/publish",
                data=b"",
                headers={"User-Agent": USER_AGENT},
                method="POST",
            )
            try:
                with opener.open(pub, timeout=60) as response:
                    response.read()
                published += 1
            except urllib.error.HTTPError as exc:
                print(f"  发布失败 asset={asset_id}: HTTP {exc.code}", file=sys.stderr)
    engine.dispose()
    return registered, published


def cached_data_rows(path: Path) -> int:
    """缓存 TSV 已有数据行数（不含首行表头；文件不存在按 0，触发重拉）。"""
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as handle:
        return sum(1 for _ in handle) - 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open Food Facts dump 清洗灌入（ODbL）")
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--dump-url", default=DUMP_URL)
    parser.add_argument("--fixture", type=Path, help="本地 TSV（dump 表头子集，离线）")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--user", default="operator")
    parser.add_argument("--pass", dest="password", default="operator123")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.fixture:
        rows = load_fixture_tsv(args.fixture, args.n)
        print(f"fixture {args.fixture} → {len(rows)} 条干净行")
    elif cached_data_rows(args.cache) >= args.n:
        # 缓存干净行已够本轮（缓存由本脚本写出、首行是表头），免再拉全量 dump
        rows = load_fixture_tsv(args.cache, args.n)
        print(f"缓存 {args.cache} → {len(rows)} 条")
    else:
        print(f"流式拉取 dump {args.dump_url}（凑满 {args.n} 条即停）…")
        rows = stream_dump(args.dump_url, args.n)
        write_clean_tsv(rows, args.cache)
        print(f"干净行 {len(rows)} → {args.cache}")
    if not rows:
        print("清洗后 0 行", file=sys.stderr)
        return 1
    if args.load or args.register:
        if not args.db:
            print("--load/--register 需要 --db 或 DATABASE_URL", file=sys.stderr)
            return 2
        inserted, skipped = load_products(args.db, rows)
        print(f"products inserted={inserted} skipped={skipped}")
    if args.register:
        registered, published = register_and_publish(
            args.api, args.user, args.password, args.db, rows, publish=args.publish
        )
        print(f"register={registered} publish={published}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
