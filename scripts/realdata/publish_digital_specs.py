"""数码外设规格文档：Wikidata attrs → 治理通道登记/人洗确认/发布写回（第 90 刀）。

数据源：fetch_wikidata_products.py 的 out/products.csv attrs 列（P176 制造商/
P571 上市/P2048/P2049 高宽，CC0）。只处理「有品牌的耳机/显示器」（主力类目
先做已发布规格资产；键盘/鼠标按需后续同通道补）。

通道：与第 33/74 刀同一条治理链——POST /api/assets/register（挂 productId，
登记即同步机洗）→ 机洗对 品牌/上市年份/高度/宽度 **弃权**（设计内：FIELD_
EXTRACTORS 只有 净含量/保质期/材质 三枚，0010 无验证抽取器的字段不冒充）
→ PATCH fields 人洗确认（source=human，值=CSV attrs，与文档正文一致）→
POST publish（必填闸门过 → 切块入索引 → 商品 spec_values 写回带
{version, asset_id} 溯源）——顾客问「XX 是什么品牌」即可命中带引用。

幂等：按 (title, product_id) 查 assets——已发布整行跳过；未发布（半途中断）
的续走确认+发布；不重复登记。来源补写 upload→wikidata（第 55 刀拆细口径，
判据与 OFF 规格资产同形态：标题「… 规格（Wikidata）」）。

用法（仓库根目录，栈须已起）：
    uv run python scripts/realdata/publish_digital_specs.py \
        --db postgresql://suite:suite@localhost:5433/suite
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = SCRIPT_DIR / "out" / "products.csv"

USER_AGENT = "ecommerce-ai-suite-realdata/1.0 (demo data script; local repo)"
TITLE_MAX = 200  # assets.title String(200)
# 任务书口径：主力类目=耳机/显示器；规格字段顺序与 category_schema 键序一致
SPEC_CATEGORIES = ("耳机", "显示器")
FIELD_ORDER = ("品牌", "上市年份", "高度", "宽度")


def spec_text(category: str, attrs: dict[str, str]) -> str:
    """attrs → 文档正文（纯函数）。形如「品牌：Sony\\n类目：耳机」；有则附可选字段。"""
    lines = [f"品牌：{attrs['品牌']}"]
    for field in FIELD_ORDER[1:]:
        if field in attrs:
            lines.append(f"{field}：{attrs[field]}")
    lines.append(f"类目：{category}")
    return "\n".join(lines) + "\n"


def spec_title(name: str) -> str:
    """资产标题（OFF 先例形态）：「{商品名} 规格（Wikidata）」，截模型列宽。"""
    return f"{name} 规格（Wikidata）"[:TITLE_MAX]


def candidate_rows(csv_path: Path) -> list[dict[str, str]]:
    """products.csv → [{name, category, attrs}]（纯函数）：耳机/显示器且 attrs 含品牌。"""
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        rows = [
            {
                "name": row["name"],
                "category": row["category"],
                "attrs": json.loads(row["attrs"] or "{}"),
            }
            for row in csv.DictReader(handle)
        ]
    return [
        row
        for row in rows
        if row["category"] in SPEC_CATEGORIES and row["attrs"].get("品牌")
    ]


# ---------------------------------------------------------------- 网络/IO（测试不吃）


def api_login(base_url: str, username: str, password: str) -> urllib.request.OpenerDirector:
    """登录拿会话 cookie，返回带 cookie 的 opener（load_reviews 同款）。"""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/auth/login",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with opener.open(request, timeout=30) as response:
        response.read()
    return opener


def _multipart(fields: dict[str, str], filename: str, payload: bytes) -> tuple[bytes, bytes]:
    """构造 multipart/form-data 体（文件段在末尾，OFF 脚本同款）。返回 (体, boundary)。"""
    boundary = "----ecommerceSuiteDigitalSpec90"
    chunks = []
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
    return b"".join(chunks), boundary.encode()


def register_asset(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    product_id: int,
    title: str,
    body: bytes,
) -> dict[str, Any]:
    """登记规格文档（挂商品）。返回资产详情 JSON；HTTP 错抛给调用方记跳过。"""
    payload, boundary = _multipart(
        {"productId": str(product_id), "title": title}, "digital-spec.txt", body
    )
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/assets/register",
        data=payload,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with opener.open(request, timeout=60) as response:
        return json.load(response)  # type: ignore[no-any-return]


def get_asset(
    opener: urllib.request.OpenerDirector, base_url: str, asset_id: int
) -> dict[str, Any]:
    """取资产详情（versions 里挑最新未发布版，断点续跑用）。"""
    request = urllib.request.Request(
        base_url.rstrip("/") + f"/api/assets/{asset_id}",
        headers={"User-Agent": USER_AGENT},
    )
    with opener.open(request, timeout=30) as response:
        return json.load(response)  # type: ignore[no-any-return]


def confirm_fields(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    asset_id: int,
    version_no: int,
    attrs: dict[str, str],
) -> bool:
    """人洗确认：品牌+可选字段（值=attrs∩该商品 schema 的键，字符串）。"""
    body = json.dumps(
        {field: attrs[field] for field in FIELD_ORDER if field in attrs}
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + f"/api/assets/{asset_id}/versions/{version_no}/fields",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="PATCH",
    )
    try:
        with opener.open(request, timeout=30) as response:
            response.read()
        return True
    except urllib.error.HTTPError as exc:
        print(
            f"  资产 {asset_id} 字段确认失败：HTTP {exc.code} {exc.read()[:120]!r}",
            file=sys.stderr,
        )
        return False


def publish_asset(
    opener: urllib.request.OpenerDirector, base_url: str, asset_id: int
) -> bool:
    """发布（必填闸门在服务端：品牌已确认才放行）。"""
    request = urllib.request.Request(
        base_url.rstrip("/") + f"/api/assets/{asset_id}/publish",
        data=b"",
        headers={"User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with opener.open(request, timeout=60) as response:
            response.read()
        return True
    except urllib.error.HTTPError as exc:
        print(
            f"  资产 {asset_id} 发布失败：HTTP {exc.code} {exc.read()[:120]!r}",
            file=sys.stderr,
        )
        return False


def fixup_asset_sources(db_url: str) -> int:
    """登记通道定值 upload → wikidata（第 55 刀拆细口径：数据集专属词，非通用 open_dataset）。"""
    from sqlalchemy import create_engine, text

    from suite_api.db import to_sqlalchemy_url

    engine = create_engine(to_sqlalchemy_url(db_url))
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE assets SET source_kind = 'wikidata'"
                " WHERE source_kind = 'upload' AND title LIKE '%规格（Wikidata）'"
            )
        )
        changed = result.rowcount or 0
    engine.dispose()
    return changed


def run(base_url: str, username: str, password: str, db_url: str, csv_path: Path) -> int:
    from sqlalchemy import create_engine, select

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import Asset, Product

    rows = candidate_rows(csv_path)
    print(f"候选（{'+'.join(SPEC_CATEGORIES)} 且有品牌）：{len(rows)} 行")
    opener = api_login(base_url, username, password)
    engine = create_engine(to_sqlalchemy_url(db_url))
    registered = published = skipped = 0
    with engine.connect() as connection:
        for row in rows:
            try:
                product_id = connection.execute(
                    select(Product.id).where(
                        Product.name == row["name"], Product.category == row["category"]
                    )
                ).scalar_one_or_none()
                if product_id is None:
                    print(f"  商品缺失，跳过：{row['name']}（{row['category']}）", file=sys.stderr)
                    continue
                title = spec_title(row["name"])
                # 幂等锚：(title, product_id) 已存在——已发布跳过，未发布续走确认+发布
                existing = connection.execute(
                    select(Asset.id, Asset.status).where(
                        Asset.title == title, Asset.product_id == product_id
                    )
                ).first()
                if existing is not None:
                    if existing.status == "published":
                        skipped += 1
                        continue
                    asset = get_asset(opener, base_url, existing.id)
                else:
                    asset = register_asset(
                        opener, base_url, product_id, title, spec_text(
                            row["category"], row["attrs"]
                        ).encode("utf-8")
                    )
                    registered += 1
                asset_id = int(asset["id"])
                versions = asset.get("versions") or []
                version_no = max((v.get("version_no", 0) for v in versions), default=0)
                if version_no < 1:
                    print(f"  资产 {asset_id} 无可用版本，跳过", file=sys.stderr)
                    continue
                if not confirm_fields(opener, base_url, asset_id, version_no, row["attrs"]):
                    continue
                if publish_asset(opener, base_url, asset_id):
                    published += 1
            except urllib.error.HTTPError as exc:
                # 单行失败不拖垮整跑（OFF 先例形态）；幂等锚保证重跑自愈
                print(
                    f"  {row['name']} 失败：HTTP {exc.code} {exc.read()[:120]!r}（可重跑自愈）",
                    file=sys.stderr,
                )
            except urllib.error.URLError as exc:
                print(f"  {row['name']} 网络失败：{exc.reason!r}（可重跑自愈）", file=sys.stderr)
    engine.dispose()
    print(
        f"汇总：登记 {registered}，发布 {published}，已发布跳过 {skipped}（候选 {len(rows)}）"
    )
    changed = fixup_asset_sources(db_url)
    print(f"来源补写：wikidata {changed} 条")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="数码外设规格文档治理发布（第 90 刀）")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="fetch 输出的 products.csv")
    parser.add_argument(
        "--api", default="http://localhost:8000", help="API 基址（默认 http://localhost:8000）"
    )
    parser.add_argument("--user", default="operator", help="操作者用户名（默认 operator）")
    parser.add_argument("--pass", dest="password", default="operator123", help="操作者密码")
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL"),
        help="演示库 URL（默认取 DATABASE_URL 环境变量）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db:
        print("错误：需要 --db 或 DATABASE_URL 环境变量", file=sys.stderr)
        return 2
    if not args.csv.exists():
        print(f"错误：{args.csv} 不存在（先跑 fetch_wikidata_products.py）", file=sys.stderr)
        return 2
    return run(args.api, args.user, args.password, args.db, args.csv)


if __name__ == "__main__":
    raise SystemExit(main())
