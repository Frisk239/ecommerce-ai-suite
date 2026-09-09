"""ABCD 客服对话灌入（多来源真实数据刀 II）：下载→抽样→转写→直调登记服务。

数据源与许可：ABCD（ASAPP，https://github.com/asappresearch/abcd ，MIT）——
1 万+ 英文人机客服对话（train/dev/test 三切分，每 session 含
convo_id/scenario/original 轮次）。MIT 可入仓库演示，保留出处链接。

通道映射：对话走「会话回流通道」——与 POST /api/sessions/{id}/register 同一
register_asset(kind=dialogue, source_kind=session_backflow) 骨架：字节落对象
存储 → LLM 抽 QA 草稿推进待人洗（未配置模型=弃权降级，资产照常待人洗）。
脚本不建 Session 行（ABCD 不是本店会话），只借登记语义灌对话资产。

网络/依赖：标准库 urllib（零新增依赖，respect HTTPS_PROXY 环境变量）；
数据为 gzip json（~37MB，缓存 out/）。机洗需要 LLM 凭证：脚本默认自动读
仓库根 .env（LLM_API_KEY/LLM_BASE_URL 进环境；已设的环境变量优先），
无 key 时 QA 弃权降级不崩。storage root 必须与 API 容器一致（compose 挂
./data/objects），API 才能读回字节。

用法（仓库根目录，先起栈：docker compose up -d）：
    uv run python scripts/realdata/load_abcd_dialogues.py --n 60     # 下载→抽样→转写
    uv run python scripts/realdata/load_abcd_dialogues.py --n 5 --register \
        --db postgresql://suite:suite@localhost:5433/suite           # 真灌对话资产
    uv run python scripts/realdata/load_abcd_dialogues.py --n 5 --register --publish 3 \
        --api http://localhost:8000                                  # 登记→确认 QA→发布
"""

from __future__ import annotations

import argparse
import gzip
import http.cookiejar
import json
import os
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
OUT_DIR = SCRIPT_DIR / "out"
DEFAULT_CACHE_GZ = OUT_DIR / "abcd_v1.1.json.gz"
DEFAULT_OUT_TXT = OUT_DIR / "abcd_transcripts.txt"

# v1.2 路径已 404（2026-09-09 实测），仓库现物为 v1.1 gzip
DEFAULT_SRC_URL = "https://raw.githubusercontent.com/asappresearch/abcd/master/data/abcd_v1.1.json.gz"
USER_AGENT = "ecommerce-ai-suite-realdata/1.0 (demo data script; local repo)"

DEFAULT_N = 60
DEFAULT_SEED = 42
# 转写说话人映射：与会话回流端点同一口径（顾客：/客服：）
SPEAKER_ZH = {"customer": "顾客", "agent": "客服"}
SPEECH_AUTHORS = ("customer", "agent")  # action=系统动作非话语，不入转写
TITLE_HEAD_CHARS = 30  # title = {flow}/{subflow} · 首问截断
TITLE_MAX = 200  # assets.title String(200)


# ---------------------------------------------------------------- 纯函数（可单测）


def parse_abcd_sessions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """ABCD 全量 json → 会话行列表（纯函数）。

    三切分按 train→dev→test 展平；每行 {convo_id, flow, subflow, turns}，
    turns 只留 customer/agent 话语（action 是系统动作非话语），空话语跳过。
    """
    sessions: list[dict[str, Any]] = []
    for split in ("train", "dev", "test"):
        for raw in payload.get(split, []):
            scenario = raw.get("scenario") or {}
            turns = [
                (str(author), str(text).strip())
                for author, text in raw.get("original", [])
                if str(author) in SPEECH_AUTHORS and str(text).strip()
            ]
            sessions.append(
                {
                    "convo_id": raw.get("convo_id"),
                    "flow": str(scenario.get("flow") or ""),
                    "subflow": str(scenario.get("subflow") or ""),
                    "turns": turns,
                }
            )
    return sessions


def transcript_of(session: dict[str, Any]) -> str:
    """会话行 → 转写文本（纯函数）：全部话语拼「顾客：…/客服：…」按行。

    与会话回流端点同构（检索切块按行/轮消费同一格式）。
    """
    return "\n".join(f"{SPEAKER_ZH[author]}：{text}" for author, text in session["turns"])


def first_question_of(session: dict[str, Any]) -> str:
    """首个顾客话语（ABCD 开头常是客服问候，顾客首问才是对话主题）。"""
    for author, text in session["turns"]:
        if author == "customer":
            return text
    return session["turns"][0][1] if session["turns"] else ""


def title_of(session: dict[str, Any]) -> str:
    """会话行 → 资产 title（纯函数）：`{flow}/{subflow} · {首问截 30 字}`。

    scene=ABCD scenario 的 flow/subflow（售后意图标签，演示可读）；无首问
    兜底「ABCD 对话」。截断到 assets.title 列宽 200。
    """
    scene = "/".join(part for part in (session["flow"], session["subflow"]) if part)
    question = first_question_of(session)[:TITLE_HEAD_CHARS]
    head = f"{scene} · {question}" if question else (scene or "ABCD 对话")
    return head[:TITLE_MAX]


def sample_sessions(
    sessions: list[dict[str, Any]], n: int, seed: int = DEFAULT_SEED
) -> list[dict[str, Any]]:
    """固定种子抽样 min(n, len(sessions)) 个会话（可复现；n 超总量返回全量）。"""
    eligible = [s for s in sessions if s["turns"]]
    if n >= len(eligible):
        return list(eligible)
    return random.Random(seed).sample(eligible, n)


def write_transcripts_txt(
    sessions: list[dict[str, Any]], out_path: Path
) -> None:
    """抽样会话 → 转写文本文件（人可查；=== title 分隔，covo_id 可追溯）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        f"=== {title_of(s)}（convo_id={s['convo_id']}）\n{transcript_of(s)}" for s in sessions
    ]
    out_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 网络/IO（测试不吃）


def download_gz(url: str, cache_path: Path) -> bytes:
    """下载 gzip json 并写缓存（已缓存则直接读缓存）。返回解压后 json 字节。"""
    if cache_path.exists():
        print(f"使用缓存：{cache_path}")
        return gzip.decompress(cache_path.read_bytes())
    print(f"下载：{url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = response.read()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    print(f"已缓存：{cache_path}（{len(data) / 1024 / 1024:.0f} MB）")
    return gzip.decompress(data)


def load_env_file(path: Path) -> int:
    """极简 .env 解析（纯标准库）：KEY=VALUE 行 os.environ.setdefault。

    已设的环境变量优先（不覆盖）；# 注释与空行跳过；值剥成对引号。返回载入
    条数。机洗 LLM 凭证与 DATABASE_URL/STORAGE_ROOT 默认从仓库根 .env 来。
    """
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


def register_dialogues(
    db_url: str, storage_root: str, sessions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """直连演示库逐会话调 register_asset（回流语义）。返回逐资产报告行。

    与回流端点同一函数（suite_api 从 uv workspace 解析）：字节落 storage root
    （与 API 容器共享目录），kind=dialogue + source_kind=session_backflow，
    机洗=LLM 抽 QA 草稿。终态由本函数 commit 收口（对齐路由层）。
    """
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import create_database_engine
    from suite_api.models import AssetVersion
    from suite_api.services.registration import register_asset
    from suite_platform.storage import LocalDirectoryStorage

    engine = create_database_engine(db_url)
    db = sessionmaker(bind=engine)()
    storage = LocalDirectoryStorage(Path(storage_root))
    reports: list[dict[str, Any]] = []
    try:
        for session in sessions:
            asset = register_asset(
                db,
                storage,
                kind="dialogue",
                title=title_of(session),
                content_bytes=transcript_of(session).encode("utf-8"),
                filename=f"abcd-{session['convo_id']}.txt",
                product_id=None,
                source_kind="session_backflow",
            )
            db.commit()  # 终态推进收口（register_asset 内部只在机洗前 commit）
            version = db.scalar(
                select(AssetVersion).where(AssetVersion.asset_id == asset.id)
            )
            qa_entry = (version.extracted_fields or {}).get("qa_pairs") or {}
            reports.append(
                {
                    "asset_id": asset.id,
                    "status": asset.status,
                    "last_error": asset.last_error,
                    "qa_pairs": qa_entry.get("value") or [],
                }
            )
    finally:
        db.close()
        engine.dispose()
    return reports


def api_login(
    base_url: str, username: str, password: str
) -> urllib.request.OpenerDirector:
    """登录拿会话 cookie（与 load_reviews.py 同一形态，复用签名 cookie 口径）。"""
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


def _request_json(
    opener: urllib.request.OpenerDirector, url: str, method: str, body: bytes | None = None
) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=120) as response:
            return json.load(response)  # type: ignore[no-any-return]
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def confirm_and_publish(
    base_url: str,
    opener: urllib.request.OpenerDirector,
    asset_id: int,
) -> str:
    """单资产：GET 详情拿 extracted qa_pairs → PATCH 确认（source=human）→ 发布。

    返回结果描述（published / 确认了几对 QA / 失败原因）。弃权（无 key 或
    无可抽）按空数组确认——「确认没有 QA」是合法人洗动作（0009）。
    """
    base = base_url.rstrip("/")
    detail = _request_json(opener, f"{base}/api/assets/{asset_id}", "GET")
    version_no = detail["versions"][-1]["version_no"]
    qa_entry = detail["versions"][-1]["extracted_fields"].get("qa_pairs") or {}
    pairs = qa_entry.get("value") or []
    body = json.dumps({"qa_pairs": pairs}).encode("utf-8")
    _request_json(
        opener, f"{base}/api/assets/{asset_id}/versions/{version_no}/fields", "PATCH", body
    )
    _request_json(opener, f"{base}/api/assets/{asset_id}/publish", "POST", b"")
    return f"确认 QA {len(pairs)} 对并发布" if pairs else "确认无 QA（空数组）并发布"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ABCD 客服对话下载/转写/回流登记（MIT）")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help=f"抽样会话数（默认 {DEFAULT_N}）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="抽样随机种子（默认 42）")
    parser.add_argument("--src", default=DEFAULT_SRC_URL, help="gzip json 下载地址")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_GZ, help="gzip 缓存路径")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_TXT, help="转写输出文本路径")
    parser.add_argument(
        "--register", action="store_true", help="直连 DB 调 register_asset 登记对话资产"
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL"),
        help="目标库 URL（默认 .env/DATABASE_URL）；宿主 compose 用 "
        "postgresql://suite:suite@localhost:5433/suite",
    )
    parser.add_argument(
        "--storage-root",
        default=os.environ.get("STORAGE_ROOT") or "./data/objects",
        help="对象存储根（默认 .env/STORAGE_ROOT 或 ./data/objects；须与 API 容器一致）",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPO_ROOT / ".env",
        help="启动前载入的 .env（默认仓库根 .env，LLM 机洗凭证来源；已设环境变量优先）",
    )
    parser.add_argument(
        "--publish",
        type=int,
        metavar="K",
        default=0,
        help="登记后对最新 K 个资产经 API 确认 QA 并发布（须与 --register 同跑；演示常用 8）",
    )
    parser.add_argument("--api", default="http://localhost:8000", help="API 基址（默认 http://localhost:8000）")
    parser.add_argument("--user", default="operator", help="操作者用户名（默认 operator）")
    parser.add_argument("--pass", dest="password", default="operator123", help="操作者密码")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # .env 先于 argparse 载入（--db/--storage-root 默认值从环境来）
    default_env = REPO_ROOT / ".env"
    loaded = load_env_file(default_env)
    if loaded:
        print(f"已从 {default_env} 载入 {loaded} 个环境变量（LLM 机洗凭证/库址）")
    args = parse_args(argv)
    if args.publish and not args.register:
        print("错误：--publish 须与 --register 同跑（从本次登记的资产里抽样）", file=sys.stderr)
        return 2
    if args.n < 1:
        print("错误：--n 须 ≥ 1", file=sys.stderr)
        return 2

    if args.env_file != default_env:
        extra = load_env_file(args.env_file)
        if extra:
            print(f"已从 {args.env_file} 补载 {extra} 个环境变量")
    if args.register and not args.db:
        print("错误：--register 需要 --db 或 DATABASE_URL（.env）", file=sys.stderr)
        return 2

    payload = json.loads(download_gz(args.src, args.cache))
    sessions = parse_abcd_sessions(payload)
    turns_total = sum(len(s["turns"]) for s in sessions)
    print(f"解析会话：{len(sessions)} 个（话语 {turns_total} 轮）")
    sampled = sample_sessions(sessions, args.n, args.seed)
    write_transcripts_txt(sampled, args.out)
    print(f"抽样 {len(sampled)} 个（种子 {args.seed}），转写已写出：{args.out}")
    if not args.register:
        print("未指定 --register：到止。加 --register 直连灌库，--publish K 同跑确认 QA+发布。")
        return 0

    reports = register_dialogues(args.db, args.storage_root, sampled)
    pending = sum(1 for r in reports if r["status"] == "pending_review")
    ingested = sum(1 for r in reports if r["status"] == "ingested")
    qa_pairs_total = sum(len(r["qa_pairs"]) for r in reports)
    for r in reports:
        if r["status"] == "ingested":
            print(f"  资产 {r['asset_id']}：机洗失败停已接入（{r['last_error']}）", file=sys.stderr)
    print(
        f"登记汇总：{len(reports)} 个资产（待人洗 {pending}，停已接入 {ingested}），"
        f"QA 草稿合计 {qa_pairs_total} 对"
    )

    if args.publish > 0 and reports:
        targets = reports[-args.publish :]
        opener = api_login(args.api, args.user, args.password)
        ok = 0
        for r in targets:
            try:
                outcome = confirm_and_publish(args.api, opener, r["asset_id"])
            except RuntimeError as exc:
                print(f"  资产 {r['asset_id']}：{exc}", file=sys.stderr)
                continue
            ok += 1
            print(f"  资产 {r['asset_id']}：{outcome}")
        print(f"发布汇总：成功 {ok}/{len(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
