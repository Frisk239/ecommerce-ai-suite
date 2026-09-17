"""OFF 规格资产「标题/正文品牌」错配的诊断与订正（第 100 刀，清审计刀 16 认账的债）。

病根（数据事实，非脚本 bug）：OFF 众包 dump 同一行里 ``product_name`` 与 ``brands``
互相矛盾（如条码 000000000063 行 name="M&M white"、brands="Fitpiggy"），第 33 刀导入
忠实转写——标题锚了 name、正文锚了 brands。后果：同一具名问句（「M&M white 的条码」）
时答时拒（审计刀 16 C 轴实测 6 次 3 拒）——标题与正文证据不互证时，实体亲和（79 刀）
的命中归宿取决于残余词法碰撞，随机。**导入脚本本身没有 bug，不改源头**（Out：数据债
在存量；重导同批行仍会带入同样矛盾——那是 OFF 数据质量与抽样策略的记债，见报告）。

判据（纯函数，离线可测）：剥掉标题「 规格（OFF）」后缀取商品名 token（小写、按
非字母数字切段），与正文字段行 ``品牌：X`` 的值 token 比对——**任一对 token 相等，
或最长公共子串 ≥4 字符（词干互证）**即判匹配；token 交集为空即疑似错配。词干口径的
理由：OFF 众包里「品牌含品名词干」是正常形态（Erdbeeren/BeerenBrüder 共享 beeren
——同一件商品的品牌-品名对，不该订正）；而 3 字符在拉丁串里随机碰撞率高（实测本库
「Graines de Chia」与「Nestle Carnation」共享 nes——完全无关的两件商品被 3 字符撞上，
正是本刀要清的错配形态）、实测全部真错配对的最长公共子串 ≤3（M&M white/Fitpiggy
共享 it、Cardiofitmd/1MD 共享 md、Graines/Nestle 共享 nes），4 字符恰好把两类分开
（代价：Nesquik/Nestlé 共享 nes=3 被判错配、订正为「Nestlé 规格（OFF）」——以正文
为准的纪律下可接受，具体产品名让位于品牌一致性）。

订正（--apply，默认 dry-run 只诊断）：**以正文为准**——错配资产标题改为
``{正文品牌} 规格（OFF）``（保持后缀形态：来源补写与标题锚依赖它）。

三个调研定案（任务书「查证后按实况做」的兑现）：
1. ``assets.title`` 是纯资产列，**不进版本快照**（``asset_versions`` 无 title 列）——
   直接 UPDATE 合法，不是篡改不可变字节；
2. **无需重发布刷新切块**：``retrieval_chunks`` 的块=正文切块+确认字段块，不含标题；
   79 刀实体亲和重排实时读 ``assets.title``，85 刀语料快照缓存的键含 (id,指针,标题)
   序列摘要——改列即改亲和，缓存自动失效，一次 UPDATE 全出口生效；
3. 正文品牌取**当前已发布指针版本**的 ``品牌：X`` 检索块（发布事务写入，就是检索与
   回答实际用的证据），不读对象存储——本地裸跑（对象在 compose 卷里）同样可跑。

audit 留痕：``audit_log`` 无 note 列、无脚本插行先例；action 用词表外值
``title_correct``（数据订正不是发布/验证，不冒充既有语义；资产时间线对未知 action
原样透传——前端 ``auditActionLabel`` 兜底直显，正合「手动备注」）。operator 取种子
操作者。

幂等：订正后标题商品名=正文品牌，判据自洽判 match——重跑 mismatch 集合为空，
UPDATE 0 行、不再插 audit。

用法（仓库根）：
    uv run python scripts/realdata/correct_off_mismatch.py --db postgresql://suite:suite@localhost:5433/suite
    uv run python scripts/realdata/correct_off_mismatch.py --db ... --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

SCRIPT_NAME = "correct_off_mismatch"
# 与 generate_golden.subject 同源的后缀形态（全角/半角括号都认：历史导入两种都出现过）
OFF_TITLE_SUFFIXES = (" 规格（OFF）", " 规格(OFF)")
FULL_SUFFIX = " 规格（OFF）"
TITLE_MAX = 200  # assets.title String(200)，与 load_openfoodfacts 截断口径一致
# 词干互证的最短公共子串（字符）：见模块 docstring 的定标理由（3 字符有 nes 类随机
# 碰撞实证，真错配对实测 ≤3，同源词干 beeren=6）
MUTUAL_STEM_MIN = 4
# audit 的手动备注 action（词表外值，见模块 docstring 定案 3 之后的说明）
AUDIT_ACTION = "title_correct"

# 判定值（表形状的稳定枚举，进诊断表与测试）
VERDICT_MATCH = "match"
VERDICT_MISMATCH = "mismatch"
VERDICT_NO_BRAND = "no_brand"  # 正文无「品牌：」行——无锚不可判，不订正不猜

BRAND_FIELD = "品牌"


# ---------------------------------------------------------------- 纯函数（离线可测）


def subject_from_title(title: str | None) -> str:
    """标题 → 商品名：剥「 规格（OFF）」后缀（全/半角），strip。无后缀原样返回。

    剥后为空（或整串就是无空格后缀形态）= 无商品名——返回空串（防御位：实库标题
    由导入拼成 ``{name} 规格（OFF）``，name 非空，此形态不该出现，出现也不猜）。
    """
    text = (title or "").strip()
    for suffix in OFF_TITLE_SUFFIXES:
        if text.endswith(suffix):
            stripped = text[: -len(suffix)].strip()
            return stripped  # 空串即「无商品名」，不回退到整串（那会把后缀当商品名）
    if text in ("规格（OFF）", "规格(OFF)"):  # 整串恰为后缀（strip 掉了前导空格的形态）
        return ""
    return text


def name_tokens(text: str) -> list[str]:
    """词法 token：小写、按非字母数字切段、去空段。M&M white -> [m, m, white]。"""
    return [token for token in re.split(r"[^0-9a-z]+", text.lower()) if token]


def longest_common_substring(left: str, right: str) -> int:
    """最长公共子串长度（经典 DP；两串都短，O(len*len) 无所谓）。"""
    best = 0
    previous = [0] * (len(right) + 1)
    for ch_l in left:
        current = [0]
        for j, ch_r in enumerate(right, 1):
            current.append(previous[j - 1] + 1 if ch_l == ch_r else 0)
            if current[-1] > best:
                best = current[-1]
        previous = current
    return best


def tokens_co_support(title_tokens: list[str], brand_tokens: list[str]) -> bool:
    """标题 token 与品牌 token 是否互证：任一对相等，或共享 ≥MUTUAL_STEM_MIN 词干。"""
    for t in title_tokens:
        for b in brand_tokens:
            if t == b:
                return True
            if longest_common_substring(t, b) >= MUTUAL_STEM_MIN:
                return True
    return False


def judge_mismatch(title: str | None, brand: str | None) -> str:
    """错配判定（纯函数）。

    - 正文品牌为空/空白 -> no_brand（无锚不可判：不订正不猜，0009 弃权不冒充同族）
    - 标题剥后缀后无商品名 -> no_brand（同上，防御位）
    - token 互证 -> match；否则 mismatch
    """
    name = subject_from_title(title)
    brand_value = (brand or "").strip()
    if not name or not brand_value:
        return VERDICT_NO_BRAND
    if tokens_co_support(name_tokens(name), name_tokens(brand_value)):
        return VERDICT_MATCH
    return VERDICT_MISMATCH


def corrected_title(brand: str) -> str:
    """订正标题：``{正文品牌} 规格（OFF）``，截断到模型列宽（品牌实测皆短，防御位）。"""
    stem = brand.strip()
    return (stem + FULL_SUFFIX)[:TITLE_MAX]


def brand_from_chunk(chunk: str) -> str:
    """「品牌：X」块行 → 值 X（strip）。非该形态返回空串。"""
    for sep in ("：", ":"):
        if chunk.startswith(f"{BRAND_FIELD}{sep}"):
            return chunk.split(sep, 1)[1].strip()
    return ""


def diagnose(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """资产行（dict：id/title/brand）→ 诊断表行（含判定与建议新标题）。

    行形状（诊断表契约，进测试）：{asset_id, title, subject, brand, verdict, new_title}。
    """
    rows: list[dict[str, Any]] = []
    for asset in assets:
        verdict = judge_mismatch(asset.get("title"), asset.get("brand"))
        rows.append(
            {
                "asset_id": asset["id"],
                "title": asset.get("title") or "",
                "subject": subject_from_title(asset.get("title")),
                "brand": (asset.get("brand") or "").strip(),
                "verdict": verdict,
                "new_title": (
                    corrected_title(asset["brand"]) if verdict == VERDICT_MISMATCH else None
                ),
            }
        )
    return rows


def format_diagnosis_table(rows: list[dict[str, Any]]) -> str:
    """诊断表（stdout 共用）：id / 标题商品名 / 正文品牌 / 判定 / → 新标题。"""
    lines = [
        f"{'资产':>5}  {'标题商品名':<40}  {'正文品牌':<20}  {'判定':<10}  订正标题"
    ]
    for row in rows:
        new_title = row["new_title"] or "-"
        lines.append(
            f"{row['asset_id']:>5}  {row['subject'][:40]:<40}  {row['brand'][:20]:<20}"
            f"  {row['verdict']:<10}  {new_title}"
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    summary = ", ".join(f"{key}={counts.get(key, 0)}" for key in (VERDICT_MISMATCH, VERDICT_NO_BRAND, VERDICT_MATCH))
    lines.append(f"共 {len(rows)} 份：{summary}")
    return "\n".join(lines)


# ---------------------------------------------------------------- DB 读取 / 订正


def load_off_assets(db: Any) -> list[dict[str, Any]]:
    """已发布 OFF 规格资产 + 其当前指针版本的「品牌：X」块值（按 asset_id 稳定排序）。

    只圈已发布：检索/演示语义只关心已发布（块也只在发布事务里写入）；待人洗 OFF
    资产没有块可判，发布时会以（订正过的）标题为锚吗——不会：标题是资产列，订正
    一次对后续发布同样生效。
    """
    from sqlalchemy import select

    from suite_api.models import Asset, AssetVersion, RetrievalChunk

    assets = db.execute(
        select(Asset.id, Asset.title)
        .join(AssetVersion, AssetVersion.id == Asset.current_published_version_id)
        .where(
            Asset.source_kind == "openfoodfacts",
            Asset.status == "published",
            Asset.current_published_version_id.isnot(None),
            Asset.title.like("%规格（OFF）") | Asset.title.like("%规格(OFF)"),
        )
        .order_by(Asset.id)
    ).all()
    brand_chunks = db.execute(
        select(RetrievalChunk.asset_id, RetrievalChunk.seq, RetrievalChunk.chunk)
        .join(
            AssetVersion,
            (AssetVersion.asset_id == RetrievalChunk.asset_id)
            & (AssetVersion.version_no == RetrievalChunk.version_no),
        )
        .join(
            Asset,
            (Asset.id == AssetVersion.asset_id)
            & (Asset.current_published_version_id == AssetVersion.id),
        )
        .where(RetrievalChunk.chunk.like(f"{BRAND_FIELD}：%"))
        .order_by(RetrievalChunk.asset_id, RetrievalChunk.seq)
    ).all()
    first_brand = {asset_id: brand_from_chunk(chunk) for asset_id, _seq, chunk in brand_chunks}
    return [{"id": asset_id, "title": title, "brand": first_brand.get(asset_id, "")}
            for asset_id, title in assets]


def apply_corrections(db: Any, rows: list[dict[str, Any]]) -> int:
    """订正错配行：UPDATE assets.title + 每行一条 audit 手动备注（同一事务）。

    只吃 diagnose 产出的 mismatch 行；调用方先诊断后订正（幂等由判据保证：订正后
    subject=brand，judge 判 match，重跑集合为空）。返回实际订正行数。
    """
    from sqlalchemy import select

    from suite_api.models import Asset, AuditLog, Operator

    operator_id = db.execute(
        select(Operator.id).order_by(Operator.id).limit(1)
    ).scalar_one_or_none()
    mismatches = [row for row in rows if row["verdict"] == VERDICT_MISMATCH]
    changed = 0
    for row in mismatches:
        result = db.execute(
            Asset.__table__.update()
            .where(Asset.id == row["asset_id"])
            .values(title=row["new_title"])
        )
        changed += result.rowcount or 0
        if operator_id is not None:
            db.add(
                AuditLog(
                    operator_id=operator_id,
                    asset_id=row["asset_id"],
                    version_no=None,
                    action=AUDIT_ACTION,
                )
            )
    return changed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OFF 规格资产标题/正文品牌错配诊断与订正")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--apply", action="store_true", help="真订正（默认 dry-run 只诊断）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db:
        print("需要 --db 或 DATABASE_URL", file=sys.stderr)
        return 2
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url

    engine = create_engine(to_sqlalchemy_url(args.db))
    with sessionmaker(bind=engine)() as db:
        rows = diagnose(load_off_assets(db))
        print(format_diagnosis_table(rows))
        mismatches = [row for row in rows if row["verdict"] == VERDICT_MISMATCH]
        if not args.apply:
            print(f"dry-run：疑似错配 {len(mismatches)} 份（--apply 执行订正）")
        elif mismatches:
            changed = apply_corrections(db, rows)
            db.commit()
            print(f"已订正 {changed} 份标题（audit 手动备注 action={AUDIT_ACTION} 同事务落账）")
        else:
            print("无可订正行（幂等：此前订正已生效或本库无错配）")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
