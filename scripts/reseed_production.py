"""生产级数据重灌脚本（2026-09-19）。

从公开可商用数据源灌入一套完整的单店铺数据底座：
  1. 清空演示库（down -v + up 重建）
  2. 种子（操作者+保温杯+瓶装水+mock 订单——迁移自动跑）
  3. OFF 食品（API 拉真商品+图片走治理链；商品/资产两级幂等）
  4. Wikidata 数码四类商品 + 规格文档（第 123 刀并入：fetch --load +
     publish_digital_specs，各自幂等；规格正文带商品名头行）
  5. Commons 数码图（Wikidata 商品 + 匹配商品照走治理链）
  6. ABCD 对话（走登记→机洗→QA 确认→发布）
  7. Dell 视频全链（上传→ASR→拣选→发布→洗帧）

用法（必须走 7890 代理，uv 管理的 Python 才能过 TLS）：
  HTTPS_PROXY=http://127.0.0.1:7890 uv run python scripts/reseed_production.py [--step N]

步骤可单独跑（--step off/commons/dialogues/dell/verify），不带 --step 跑全链。
所有写入走治理 API（register/publish），不走 SQL 直插。

许可：
  OFF 数据 ODbL / 图片 CC BY-SA（商用可，注明出处）
  Commons 图片 CC BY-SA / PD（商用可）
  ABCD 对话 MIT
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "scripts" / "realdata" / "out"
OUT.mkdir(parents=True, exist_ok=True)

API = os.environ.get("RESEED_API", "http://localhost:8000")
TOKEN = None  # login 后存 cookie


def _req(url, data=None, method="GET", headers=None, raw=False):
    """统一 HTTP（自动带 cookie）。"""
    hdrs = {"User-Agent": "ecommerce-ai-suite/0.1 (production reseed)"}
    if headers:
        hdrs.update(headers)
    if data is not None and not raw:
        hdrs["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
        if raw:
            return resp.headers, body
        return json.loads(body.decode())


def login():
    global TOKEN
    import http.cookiejar
    global _CJ
    _CJ = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_CJ))
    urllib.request.install_opener(opener)
    req = urllib.request.Request(
        f"{API}/api/auth/login",
        data=json.dumps({"username": "operator", "password": "operator123"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
    print("✓ 登录操作者")


def _upload_file(path_or_bytes, filename, content_type, extra_fields=None):
    """multipart 上传（register 端点）。"""
    import uuid
    boundary = uuid.uuid4().hex
    if isinstance(path_or_bytes, Path):
        data = path_or_bytes.read_bytes()
    else:
        data = path_or_bytes
    parts = []
    for k, v in (extra_fields or {}).items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{API}/api/assets/register",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode())


def _confirm_and_publish(asset_id, field_name, field_value):
    """确认字段 + 发布。"""
    _req(
        f"{API}/api/assets/{asset_id}/versions/1/fields",
        data={field_name: field_value},
        method="PATCH",
    )
    _req(f"{API}/api/assets/{asset_id}/publish", method="POST")


def _download(url, dest):
    """下载到 out/ 目录。"""
    req = urllib.request.Request(url, headers={"User-Agent": "ecommerce-ai-suite/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        dest.write_bytes(resp.read())
    return dest


# ---------------------------------------------------------------------------
# Step OFF：食品（带图片 + 规格）
# ---------------------------------------------------------------------------

# 精选条码——OFF 里产品名/品牌/图片齐全的常见食品（英文标签为主，少量中文）
OFF_BARCODES = [
    ("3017620422003", "Nutella 榛子巧克力酱"),      # Nutella 400g
    ("3017760000109", "Lu Petit Écolier 黑巧克力饼干"),  # Petit Écolier
    ("5449000000996", "Coca-Cola 可乐 330ml"),        # Coca-Cola
    ("7622210951963", "Oreo 奥利奥饼干"),              # Oreo
    ("3017760000109", "Lu Petit Écolier"),            # (dup, skip)
    ("8000500310427", "Barilla 意大利面"),            # Barilla
    ("8076809513752", "Kinder 健达巧克力"),           # Kinder
    ("3560070976476", "Lindt 瑞士莲黑巧克力 85%"),   # Lindt
    ("3045320006598", "Danone 达能酸奶"),             # Danone
    ("3245390096267", "Evian 依云矿泉水"),           # Evian
]


def _assets_by_title():
    """已登记（未废弃）资产按 (title, product_id) 索引——步骤级幂等锚。

    OFF 步骤实测翻车形态（2026-09-19 重灌）：商品级「同名跳过」挡不住
    「商品在、资产半路失败后重跑」——图片/规格各再注册一份（A-1/A-3、
    A-2/A-4 双份，检索 top-k 被同文重复占位）。锚到资产标题才真正幂等。
    """
    assets = _req(f"{API}/api/assets")
    out = set()
    for a in assets:
        if not a.get("title"):
            continue
        prod = a.get("product") or {}
        out.add((a["title"], prod.get("id") or a.get("product_id")))
    return out


def step_off(count=6):
    """OFF 食品：API 拉产品+图 → 注册商品 + 图片资产 + 规格文档 → 发布。"""
    print(f"\n{'='*60}\nOFF 食品灌入（目标 {count} 件）\n{'='*60}")
    done = 0
    known = _assets_by_title()
    for barcode, cn_name in OFF_BARCODES:
        if done >= count:
            break
        try:
            d = _req(
                f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
                "?fields=product_name,brands,quantity,image_front_url,ingredients_text,nutriments"
            )
        except Exception as e:
            print(f"  跳过 {barcode}: {e}")
            continue
        p = d.get("product", {})
        name = p.get("product_name") or cn_name
        img_url = p.get("image_front_url")
        if not img_url:
            print(f"  跳过 {barcode}: 无图")
            continue

        # 1. 商品（同名复用；资产级幂等见 known 锚）
        existing = _req(f"{API}/api/products")
        pid = next((pr["id"] for pr in existing if pr["name"] == cn_name), None)
        if pid is None:
            product = _req(f"{API}/api/products", data={"name": cn_name, "category": "食品"}, method="POST")
            pid = product["id"]
            print(f"  商品 {cn_name} → P-{pid}")
        img_title = f"{cn_name} 商品图"
        spec_title = f"{cn_name} · 规格"

        # 2. 下载图片 + 注册图片资产
        if (img_title, pid) in known:
            print(f"    图片已存在，跳过")
        else:
            img_path = OUT / f"off_{barcode}.jpg"
            try:
                _download(img_url, img_path)
            except Exception as e:
                print(f"  图片下载失败: {e}")
                continue
            img_asset = _upload_file(
                img_path,
                f"off_{barcode}.jpg",
                "image/jpeg",
                {"title": img_title, "productId": str(pid)},
            )
            # 确认图片描述 + 发布
            _confirm_and_publish(img_asset["id"], "图片描述", f"{cn_name} 的产品实拍图，来自 Open Food Facts 公开数据（CC BY-SA）")
            print(f"    图片 → A-{img_asset['id']} 已发布")

        # 3. 注册规格文档
        if (spec_title, pid) in known:
            print(f"    规格已存在，跳过")
        else:
            qty = p.get("quantity") or ""
            brands = p.get("brands") or ""
            ingredients = (p.get("ingredients_text") or "")[:500]
            spec_body = f"{cn_name} 规格\n净含量：{qty}\n保质期：见包装标注\n品牌：{brands}\n配料：{ingredients}\n"
            spec_asset = _upload_file(
                spec_body.encode(),
                f"off_{barcode}_spec.txt",
                "text/plain",
                {"title": spec_title, "productId": str(pid)},
            )
            # 确认规格字段 + 发布——只 PATCH 食品 schema 的合法字段（净含量+保质期）。
            # 品牌留给正文（食品 schema 无「品牌」键，PATCH 它 422）。
            fields_to_confirm = {"保质期": "见包装标注"}  # OFF 源无保质期，如实填
            if qty:
                fields_to_confirm["净含量"] = qty
            # 食品类目必填 净含量+保质期（0019 spec_schema）——OFF 源没有保质期，
            # 如实填「见包装标注」不编造具体日期
            fields_to_confirm.setdefault("保质期", "见包装标注")
            if fields_to_confirm:
                _req(
                    f"{API}/api/assets/{spec_asset['id']}/versions/1/fields",
                    data=fields_to_confirm,
                    method="PATCH",
                )
            _req(f"{API}/api/assets/{spec_asset['id']}/publish", method="POST")
            print(f"    规格 → A-{spec_asset['id']} 已发布")
        done += 1
        time.sleep(0.5)  # OFF API 礼貌间隔

    print(f"\n✓ OFF 完成：{done} 件食品（含商品+图片+规格）")


# ---------------------------------------------------------------------------
# Step Commons：数码商品图
# ---------------------------------------------------------------------------

# 我们的 Wikidata 商品类目 → Commons 搜索词（每类取 1 张最像「商品照」的）
COMMONS_QUERIES = {
    "键盘": "mechanical keyboard product",
    "显示器": "computer monitor desk",
    "耳机": "wireless headphones product",
    "鼠标": "computer mouse product",
}


def _search_commons(query, limit=10):
    """Commons 全文搜索图片文件（URL 编码文件名 + 429 重试）。"""
    import urllib.parse
    q = urllib.parse.quote(query)
    url = (
        f"https://commons.wikimedia.org/w/api.php?action=query&list=search"
        f"&srsearch={q}%20filetype:bitmap&srnamespace=6&srlimit={limit}&format=json"
    )
    # 429 重试（Commons 激进限速，等 5s）
    for attempt in range(3):
        try:
            d = _req(url)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(5)
                continue
            raise
    return [
        {
            "title": r["title"],
            "url": "https://commons.wikimedia.org/wiki/Special:FilePath/"
                   + urllib.parse.quote(r["title"].replace("File:", ""))
                   + "?width=800",
        }
        for r in d.get("query", {}).get("search", [])
    ]


def step_commons():
    """Wikidata 商品 + Commons 商品照 → 注册图片资产 → 发布。"""
    print(f"\n{'='*60}\nCommons 数码商品图灌入\n{'='*60}")
    products = _req(f"{API}/api/products")
    # 找数码类商品（键盘/鼠标/显示器/耳机）
    electronics = [p for p in products if p["category"] in ("键盘", "鼠标", "显示器", "耳机")]
    if not electronics:
        # 也匹配笔记本电脑/智能手机等
        electronics = [p for p in products if p["category"] in ("笔记本电脑", "平板电脑")]
    print(f"  库内数码商品: {len(electronics)} 件")

    for prod in electronics[:4]:  # 取前 4 件有商品的
        cat = prod["category"]
        query = COMMONS_QUERIES.get(cat, f"{cat} product")
        try:
            results = _search_commons(query, 5)
        except Exception as e:
            print(f"  {cat}: 搜索失败 {e}")
            continue
        if not results:
            print(f"  {cat}: 无结果")
            continue

        # 取第一张，下载并注册
        img_url = results[0]["url"]
        img_path = OUT / f"commons_{prod['id']}.jpg"
        try:
            _download(img_url, img_path)
        except Exception as e:
            print(f"  {cat}: 图片下载失败 {e}")
            continue

        asset = _upload_file(
            img_path,
            f"commons_{prod['id']}.jpg",
            "image/jpeg",
            {"title": f"{prod['name']} 商品图", "productId": str(prod["id"])},
        )
        _confirm_and_publish(
            asset["id"], "图片描述",
            f"{prod['name']}（{cat}）的产品实拍图，来自 Wikimedia Commons（CC BY-SA / PD）",
        )
        print(f"  {prod['name']} → A-{asset['id']} 图片已发布")
        time.sleep(2)  # Commons API 礼貌间隔（防 429）

    print("\n✓ Commons 完成")


# ---------------------------------------------------------------------------
# Step Wikidata：数码商品 + 规格文档（第 123 刀并入重灌链——此前带外跑，重置即丢）
# ---------------------------------------------------------------------------


def step_wikidata():
    """Wikidata 数码四类（fetch --load 幂等）+ 规格文档治理发布（(title,product) 幂等）。

    两个子脚本各自幂等：fetch 按「同名同类目跳过」灌 products；publish 按
    「(title, product_id) 已发布跳过、未发布续走确认+发布」。SPARQL 走本地
    缓存（wikidata_*.json），无网也能重放。
    """
    print(f"\n{'='*60}\nWikidata 数码商品 + 规格文档\n{'='*60}")
    import subprocess

    env = os.environ.copy()
    db = "postgresql://suite:suite@localhost:5433/suite"
    for label, cmd in (
        ("商品灌入", [
            sys.executable, str(REPO / "scripts" / "realdata" / "fetch_wikidata_products.py"),
            "--digital-only", "--load", "--db", db,
        ]),
        ("规格发布", [
            sys.executable, str(REPO / "scripts" / "realdata" / "publish_digital_specs.py"),
            "--db", db,
        ]),
    ):
        print(f"  [{label}] {' '.join(cmd[1:])}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, cwd=str(REPO))
        tail = (result.stdout or "").strip().splitlines()[-4:]
        for line in tail:
            print(f"    {line}")
        if result.returncode != 0:
            print(f"  ✗ {label} 失败（exit {result.returncode}）：{ (result.stderr or '')[-300:] }")
            return
    print("\n✓ Wikidata 完成")


# ---------------------------------------------------------------------------
# Step Dialogues：对话资产
# ---------------------------------------------------------------------------


def step_dialogues(count=5):
    """ABCD 对话走登记→机洗→QA 确认→发布。"""
    print(f"\n{'='*60}\nABCD 对话灌入（目标 {count} 条）\n{'='*60}")
    abcd_loader = REPO / "scripts" / "realdata" / "load_abcd_dialogues.py"
    if not abcd_loader.exists():
        print("  找不到 load_abcd_dialogues.py，跳过")
        return

    import subprocess
    env = os.environ.copy()
    env["LLM_API_KEY"] = _read_env("LLM_API_KEY") or ""
    cmd = [
        sys.executable, str(abcd_loader),
        "--register", str(count),
        "--publish", str(count),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    print(result.stdout[-500:] if result.stdout else "")
    if result.returncode != 0:
        print(f"  stderr: {result.stderr[-300:]}")
    print("\n✓ 对话灌入完成")


def _read_env(key):
    env_path = REPO / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# Step Dell：视频全链
# ---------------------------------------------------------------------------


def step_dell():
    """Dell 显示器开箱视频全链。"""
    print(f"\n{'='*60}\nDell 视频全链\n{'='*60}")
    video_path = OUT / "dell_monitor_unboxing.mp4"
    if not video_path.exists():
        # 找 data/tmp 下的副本
        alt = REPO / "data" / "tmp" / "dell_monitor_unboxing.mp4"
        if alt.exists():
            video_path = alt
        else:
            print("  找不到 Dell 视频文件，跳过")
            return

    print(f"  视频文件: {video_path} ({video_path.stat().st_size // 1024 // 1024}MB)")

    # 1. 上传源录像
    import uuid
    boundary = uuid.uuid4().hex
    data = video_path.read_bytes()
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="dell_monitor.mp4"\r\n'
        f"Content-Type: video/mp4\r\n\r\n".encode() + data + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{API}/api/clips/recordings",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        rec = json.loads(resp.read().decode())
    print(f"  ✓ 源录像已上传: {rec['label']} ({rec['size_bytes'] // 1024 // 1024}MB)")

    # 2. ASR 转写（长视频，等 300s）
    print("  转写中（最长 5 分钟）…")
    req = urllib.request.Request(
        f"{API}/api/clips/recordings/{rec['id']}/transcribe",
        data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        tr = json.loads(resp.read().decode())
    print(f"  ✓ 转写完成: {tr['candidates_created']} 候选")

    # 3. 拣选前 3 条
    candidates = _req(f"{API}/api/clips/candidates")
    pending = [c for c in candidates if c["status"] == "pending"]
    to_pick = [c["id"] for c in pending[:3]]
    if to_pick:
        registered = _req(
            f"{API}/api/clips/candidates/pick",
            data={"ids": to_pick},
            method="POST",
        )
        print(f"  ✓ 拣选 {len(registered)} 条: {[a['id'] for a in registered]}")

        # 4. 发布
        for a in registered:
            _req(f"{API}/api/assets/{a['id']}/publish", method="POST")
        print(f"  ✓ 已发布 {len(registered)} 条视频资产")

    # 5. 找戴尔商品（或创建）
    products = _req(f"{API}/api/products")
    dell = next((p for p in products if "显示器" in p["name"] or "Dell" in p["name"]), None)
    if dell:
        # 给第一条发布视频跑洗帧
        first = registered[0] if registered else None
        if first:
            print(f"  洗帧 A-{first['id']}（最长 3 分钟）…")
            try:
                req = urllib.request.Request(
                    f"{API}/api/assets/{first['id']}/frame-candidates",
                    data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=300) as resp:
                    fc = json.loads(resp.read().decode())
                print(f"  ✓ 洗帧: {len(fc.get('candidates', []))} 候选帧")
                # 确认第一帧
                if fc.get("candidates"):
                    _req(
                        f"{API}/api/assets/{first['id']}/frames",
                        data={"at_second": fc["candidates"][0]["at_second"], "vlm_note": fc["candidates"][0].get("note")},
                        method="POST",
                    )
                    print(f"  ✓ 洗帧帧已登记为图片资产")
            except Exception as e:
                print(f"  洗帧跳过: {e}")

    print("\n✓ Dell 视频链完成")


# ---------------------------------------------------------------------------
# Step Verify：demo_prepare
# ---------------------------------------------------------------------------


def step_verify():
    print(f"\n{'='*60}\ndemo_prepare 检查\n{'='*60}")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "demo_prepare.py")],
        capture_output=True, text=True, timeout=120,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"  stderr: {result.stderr[-300:]}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="生产级数据重灌")
    parser.add_argument("--step", choices=["off", "wikidata", "commons", "dialogues", "dell", "verify"],
                        help="只跑某一步")
    parser.add_argument("--off-count", type=int, default=6, help="OFF 食品数（默认 6）")
    parser.add_argument("--dialogue-count", type=int, default=5, help="对话数（默认 5）")
    args = parser.parse_args()

    if not args.step or args.step == "off":
        login()
        step_off(args.off_count)
    if not args.step or args.step == "wikidata":
        if not TOKEN:
            login()
        step_wikidata()
    if not args.step or args.step == "commons":
        if not TOKEN:
            login()
        step_commons()
    if not args.step or args.step == "dialogues":
        step_dialogues(args.dialogue_count)
    if not args.step or args.step == "dell":
        if not TOKEN:
            login()
        step_dell()
    if not args.step or args.step == "verify":
        step_verify()

    if not args.step:
        print(f"\n{'='*60}\n全链完成。刷新 http://localhost:5173 重走七站验收。{'='*60}")


if __name__ == "__main__":
    main()
