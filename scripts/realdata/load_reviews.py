"""中文电商评论灌入（多来源真实数据刀 I）：下载→抽样→转 CSV→走既有批量导入 API。

数据源与许可：online_shopping_10_cats（ChineseNlpCorpus，
https://github.com/SophonPlus/ChineseNlpCorpus —— 6.2 万条中文电商评论、10 类目、
正/负情感标注）。GitHub 开放语料库，研究用途：演示请保留出处链接，不作商业用途
（详见同目录 README.md）。

通道映射：评论走「上传通道的批量形态」——登录 operator 后调既有
POST /api/assets/import-csv（200 行/批）与 POST /api/assets/{id}/publish
（评论资产不挂商品，发布无必填字段闸门）。证明批量通道吃得下真实第三方数据。

网络/依赖：标准库 urllib（零新增依赖，respect HTTPS_PROXY 环境变量）；
zip 内 csv 编码 utf-8 → gb18030（GB2312 超集）兜底。

用法（仓库根目录，先起栈：docker compose up -d）：
    uv run python scripts/realdata/load_reviews.py --n 2000          # 下载→抽样→out/reviews.csv
    uv run python scripts/realdata/load_reviews.py --n 200 --import  # 转 CSV 后登录批量导入
    uv run python scripts/realdata/load_reviews.py --n 200 --import --publish 20
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import io
import json
import random
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "out"
DEFAULT_CACHE_ZIP = OUT_DIR / "online_shopping_10_cats.zip"
DEFAULT_OUT_CSV = OUT_DIR / "reviews.csv"

DEFAULT_ZIP_URL = (
    "https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/master/"
    "datasets/online_shopping_10_cats/online_shopping_10_cats.zip"
)
USER_AGENT = "ecommerce-ai-suite-realdata/1.0 (demo data script; local repo)"
# 与 API 批量通道上限对齐（suite_api.services.csv_import.MAX_IMPORT_ROWS = 200）
BATCH_ROWS = 200
DEFAULT_N = 2000
DEFAULT_SEED = 42
TITLE_HEAD_CHARS = 18  # spec：title = {类目}评论 · {前 18 字}


# ---------------------------------------------------------------- 纯函数（可单测）


def parse_reviews_csv(text: str) -> list[tuple[str, str, str]]:
    """评论 csv 文本 → [(cat, label, review)]（纯函数）。

    表头按列名定位（cat/label/review，容 BOM）；无识别表头时按位置 0/1/2 兜底。
    逐行清洗：列数不足/空类目/空评论跳过，字段剥首尾空白。
    """
    records = list(csv.reader(io.StringIO(text)))
    if not records:
        return []
    header = [cell.strip().lstrip("\ufeff") for cell in records[0]]
    cat_idx = header.index("cat") if "cat" in header else 0
    label_idx = header.index("label") if "label" in header else 1
    review_idx = header.index("review") if "review" in header else 2
    last_idx = max(cat_idx, label_idx, review_idx)
    rows: list[tuple[str, str, str]] = []
    for record in records[1:]:
        if len(record) <= last_idx:
            continue  # 空行/缺列
        cat = record[cat_idx].strip()
        label = record[label_idx].strip()
        review = record[review_idx].strip()
        if cat and review:
            rows.append((cat, label, review))
    return rows


def read_reviews_from_zip(zip_bytes: bytes) -> list[tuple[str, str, str]]:
    """zip 字节 → 评论行（纯函数）：取 zip 内首个 csv，编码 utf-8 失败退 gb18030。"""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        csv_name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        raw = archive.read(csv_name)
    text: str | None = None
    for encoding in ("utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("评论 csv 既非 UTF-8 也非 GB18030（已含 GB2312/GBK）")
    return parse_reviews_csv(text)


def sample_reviews(
    rows: list[tuple[str, str, str]], n: int, seed: int = DEFAULT_SEED
) -> list[tuple[str, str, str]]:
    """固定种子抽样 min(n, len(rows)) 条（可复现；n 超总量返回全量）。"""
    if n >= len(rows):
        return list(rows)
    return random.Random(seed).sample(rows, n)


def review_to_title_content(cat: str, review: str) -> tuple[str, str]:
    """(cat, review) → CSV 批量导入的 (title, content)（纯函数，spec 形状）。

    title = `{类目}评论 · {评论前 18 字}`（评论短于 18 字取全文）；content = 全文。
    """
    head = review.strip()[:TITLE_HEAD_CHARS]
    return (f"{cat}评论 · {head}", review.strip())


def iter_batches(
    rows: list[tuple[str, str]], size: int = BATCH_ROWS
) -> Iterator[list[tuple[str, str]]]:
    """把 (title, content) 行切成 ≤size 的批（与 API 单次行数上限对齐）。"""
    if size < 1:
        raise ValueError("批大小须 ≥ 1")
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def build_batch_bytes(batch: list[tuple[str, str]]) -> bytes:
    """一批 (title, content) → import_csv 可受理的 CSV 字节（表头 + utf-8）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("title", "content"))
    writer.writerows(batch)
    return buffer.getvalue().encode("utf-8")


def write_reviews_csv(converted: list[tuple[str, str]], out_path: Path) -> None:
    """(title, content) 全量 → CSV（utf-8-sig，Excel 可直开；--import 另用纯 utf-8 批）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("title", "content"))
        writer.writerows(converted)


# ---------------------------------------------------------------- 网络/IO（测试不吃）


def download_zip(url: str, cache_path: Path) -> bytes:
    """下载 zip 并写缓存（已缓存则直接读缓存）。返回 zip 字节。"""
    if cache_path.exists():
        print(f"使用缓存：{cache_path}")
        return cache_path.read_bytes()
    print(f"下载：{url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    print(f"已缓存：{cache_path}（{len(data) / 1024:.0f} KB）")
    return data


def api_login(
    base_url: str, username: str, password: str
) -> urllib.request.OpenerDirector:
    """登录拿会话 cookie（签名 httpOnly，由 CookieJar 持有），返回带 cookie 的 opener。"""
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


def _multipart_bytes(field: str, filename: str, payload: bytes) -> tuple[bytes, bytes]:
    """构造 multipart/form-data 文件上传体。返回 (请求体, boundary)。"""
    boundary = "----ecommerceSuiteRealdata7d9f2a"
    parts = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode()
    return parts + payload + f"\r\n--{boundary}--\r\n".encode(), boundary.encode()


def post_import_batch(
    opener: urllib.request.OpenerDirector, base_url: str, batch: list[tuple[str, str]]
) -> tuple[list[int], int]:
    """一批 (title, content) → POST /api/assets/import-csv。返回 (created 资产 id, skipped 数)。"""
    body, boundary = _multipart_bytes("file", "reviews-batch.csv", build_batch_bytes(batch))
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/assets/import-csv",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with opener.open(request, timeout=300) as response:
        report: dict[str, Any] = json.load(response)
    created_ids = [int(row["asset_id"]) for row in report.get("created", [])]
    return created_ids, len(report.get("skipped", []))


def post_publish(
    opener: urllib.request.OpenerDirector, base_url: str, asset_id: int
) -> bool:
    """发布单条资产。评论资产不挂商品无必填闸门，成功返回 True。"""
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
        print(f"  资产 {asset_id} 发布失败：HTTP {exc.code} {exc.read()[:120]!r}", file=sys.stderr)
        return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="中文电商评论下载/抽样/批量导入（研究用途）")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help=f"抽样条数（默认 {DEFAULT_N}）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="抽样随机种子（默认 42）")
    parser.add_argument("--src", default=DEFAULT_ZIP_URL, help="zip 下载地址（默认线上源）")
    parser.add_argument("--zip-file", type=Path, help="直接用本地 zip（跳过下载/缓存）")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_ZIP, help="zip 缓存路径")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_CSV, help="转换输出 CSV 路径")
    parser.add_argument("--import", dest="do_import", action="store_true", help="登录并批量导入 API")
    parser.add_argument(
        "--api", default="http://localhost:8000", help="API 基址（默认 http://localhost:8000）"
    )
    parser.add_argument("--user", default="operator", help="操作者用户名（默认 operator）")
    parser.add_argument("--pass", dest="password", default="operator123", help="操作者密码")
    parser.add_argument(
        "--publish",
        type=int,
        metavar="N",
        default=0,
        help="导入后从 created 里抽 N 条发布（须与 --import 同跑）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.publish and not args.do_import:
        print("错误：--publish 须与 --import 同跑（从本次 created 里抽样）", file=sys.stderr)
        return 2
    if args.n < 1:
        print("错误：--n 须 ≥ 1", file=sys.stderr)
        return 2

    if args.zip_file is not None:
        zip_bytes = args.zip_file.read_bytes()
        print(f"使用本地 zip：{args.zip_file}（{len(zip_bytes) / 1024:.0f} KB）")
    else:
        zip_bytes = download_zip(args.src, args.cache)

    rows = read_reviews_from_zip(zip_bytes)
    print(f"解析评论：{len(rows)} 行")
    if not rows:
        print("错误：zip 内未解析出任何评论行", file=sys.stderr)
        return 1
    sampled = sample_reviews(rows, args.n, args.seed)
    converted = [review_to_title_content(cat, review) for cat, _, review in sampled]
    write_reviews_csv(converted, args.out)
    print(f"抽样 {len(sampled)} 条（种子 {args.seed}），已转换写出：{args.out}")

    if not args.do_import:
        print("未指定 --import：到止。加 --import 登录批量导入，--publish N 同跑发布样例。")
        return 0

    opener = api_login(args.api, args.user, args.password)
    created_ids: list[int] = []
    skipped_total = 0
    batches = list(iter_batches(converted))
    for index, batch in enumerate(batches, start=1):
        batch_ids, skipped = post_import_batch(opener, args.api, batch)
        created_ids.extend(batch_ids)
        skipped_total += skipped
        print(f"  批 {index}/{len(batches)}：行 {len(batch)}，created {len(batch_ids)}，skipped {skipped}")
    print(f"导入汇总：created {len(created_ids)}，skipped {skipped_total}")

    if args.publish > 0:
        publish_ids = random.Random(args.seed).sample(created_ids, min(args.publish, len(created_ids)))
        ok = sum(1 for asset_id in publish_ids if post_publish(opener, args.api, asset_id))
        print(f"发布汇总：成功 {ok}/{len(publish_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
