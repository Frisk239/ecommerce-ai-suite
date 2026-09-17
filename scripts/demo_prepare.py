"""演示库就绪检查器（第 96 刀）：把演示库带到「体系演示就绪」的前置核验。

## 为什么

《体系闭环演示手册》（docs/demo-system-loop.md）的 12 幕全部依赖演示库里的
**既有素材**（数码店数据、演示资产 A-492/493/497/501/503/505、演示会话
#199/#331/#344/#345/#350/#353）。演示前若素材被误清/漂移，现场才发现就晚了——
本脚本在 demo_reset 之上做「演示就绪」盘点：**只核验、只报告缺失，不自动重建**。

## 为什么不自动重建（治理动作不自动）

缺失项的补救本身是治理动作（发布资产、拣选切片、回流会话……）——脚本替人
发布等于绕过「登记→机洗→人洗→发布」的闸门，正是本仓 0005/0009 要防的事。
补救命令以 `--fix-hint` 打印，由操作者执行。

## 查什么（三类）

1. **数码店数据**（第 90 刀灌入）：商品 179 / 已发布 Wikidata 规格 10（109 刀退役冗余份 A-484 后）/
   政策文档 3（数码外设保修/退换货/发票与配送）。
2. **演示资产**：A-492（刻字口径，G-78 已随发布解决）、A-493（回流对话，
   待人洗）、A-497（LK201 必填闸全链，confirm+publish 双审计行）、A-501
   （图片治理）、A-503（版本指针 v1→v2）、A-505（洗帧，clip_frame）。
3. **演示会话与直播线**：#199 主线（上班口径）、#331/#332（92 刀缺口与 OOV
   实录）、#344（再问命中）、#345（已回流→A-493）、#350（94b 双图直出）、
   #353（94c 真帧）、A-264（直播切片源）与转写候选（cloud 来源）。

## 用法

```bash
uv run python scripts/demo_prepare.py --db postgresql://suite:suite@localhost:5433/suite            # 就绪报告
uv run python scripts/demo_prepare.py --db postgresql://suite:suite@localhost:5433/suite --fix-hint # 附补救命令
```

本脚本**没有 --apply**：它是检查器不是写者（与 demo_reset 的区别——那个清
探针有明确特征判据，这个补素材是治理动作）。任何缺失都退出码 1，CI/演示
前检查可直接用退出码闸。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Any

# 90 刀灌入口径（digital-store-90-closeout §交付 5）：演示库的数码店底座。
# 第 109 刀订正：Wikidata 规格 11→10——A-484「Fairbuds 规格」是与 A-483 同字节
# 的冗余份（施工单 retire，正文同为未解析 QID 形态），退役后唯一规格 10 份；
# 口径仍是「≥」，后续新灌规格只增不减（110 刀重灌时按新数再订正）。
EXPECTED_PRODUCTS = 179
EXPECTED_WIKIDATA_SPECS = 10
POLICY_TITLES = ("数码外设保修政策", "退换货政策", "发票与配送口径")


@dataclass
class Check:
    """一项就绪检查：ok=False 时 detail 说缺什么、hint 给补救命令。"""

    name: str
    ok: bool
    detail: str
    hint: str | None = None


def _text(sql: str) -> Any:
    from sqlalchemy import text

    return text(sql)


def _one(session: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    return session.execute(_text(sql), params or {}).scalar()


def _row(session: Any, sql: str, params: dict[str, Any] | None = None) -> tuple | None:
    return session.execute(_text(sql), params or {}).first()


def _asset_row(session: Any, asset_id: int) -> tuple | None:
    """(status, kind, source_kind, 线上版本号)——按演示库固定 id 取演示资产。"""
    return _row(
        session,
        """
        SELECT a.status, a.kind, a.source_kind, pv.version_no
        FROM assets a LEFT JOIN asset_versions pv ON pv.id = a.current_published_version_id
        WHERE a.id = :id AND a.discarded_at IS NULL
        """,
        {"id": asset_id},
    )


def _check_digital_store(session: Any) -> list[Check]:
    products = int(_one(session, "SELECT count(*) FROM products") or 0)
    specs = int(
        _one(
            session,
            "SELECT count(*) FROM assets WHERE status = 'published' AND kind = 'document'"
            " AND source_kind = 'wikidata' AND discarded_at IS NULL",
        )
        or 0
    )
    policies = int(
        _one(
            session,
            "SELECT count(*) FROM assets WHERE status = 'published' AND discarded_at IS NULL"
            " AND title = ANY(:titles)",
            {"titles": list(POLICY_TITLES)},
        )
        or 0
    )
    return [
        Check(
            "数码店商品",
            products >= EXPECTED_PRODUCTS,
            f"{products}（口径 ≥{EXPECTED_PRODUCTS}，第 90 刀灌入）",
            "uv run python scripts/realdata/fetch_wikidata_products.py --digital-only --load"
            " --db postgresql://suite:suite@localhost:5433/suite",
        ),
        Check(
            "已发布 Wikidata 规格",
            specs >= EXPECTED_WIKIDATA_SPECS,
            f"{specs}（口径 ≥{EXPECTED_WIKIDATA_SPECS}）",
            "uv run python scripts/realdata/publish_digital_specs.py"
            " --db postgresql://suite:suite@localhost:5433/suite",
        ),
        Check(
            "政策文档",
            policies == len(POLICY_TITLES),
            f"{policies}/{len(POLICY_TITLES)}（{'、'.join(POLICY_TITLES)}）",
            "店主口径文档按 92 刀施工单重新上传并发布（治理台 /api/assets/register）",
        ),
    ]


def _check_demo_assets(session: Any) -> list[Check]:
    checks: list[Check] = []

    def expect_asset(name: str, asset_id: int, status: str, kind: str, hint: str) -> None:
        row = _asset_row(session, asset_id)
        ok = row is not None and row[0] == status and row[1] == kind
        detail = (
            f"A-{asset_id} {row[0]}/{row[1]}（期望 {status}/{kind}）"
            if row
            else f"A-{asset_id} 不存在或已废弃"
        )
        checks.append(Check(name, ok, detail, hint if not ok else None))

    # 幕 11 素材：G-80「有没有白色款」open 在库（OOV 落缺口的实证形态——96 刀评审补）
    g80 = _row(
        session,
        "SELECT status FROM knowledge_gaps WHERE id = 80",
    )
    checks.append(
        Check(
            "缺口 G-80（白色款·幕 11 素材）",
            g80 is not None and g80[0] == "open",
            f"G-80 {g80[0] if g80 else '不存在'}（期望 open）",
            "96 刀实证缺口行——按手册幕 11 重走一次服务类拒答",
        )
    )
    # 拒答飞轮素材（幕 1）：A-492 已发布，且 G-78 随发布解决并指回它
    expect_asset("刻字口径资产 A-492", 492, "published", "document", "按手册第一幕重走：缺口 tab 上传《定制刻字服务口径》并发布")
    gap = _row(
        session,
        "SELECT status, resolved_by_asset_id FROM knowledge_gaps WHERE id = 78",
    )
    checks.append(
        Check(
            "缺口 G-78 已随发布解决",
            gap is not None and gap[0] == "resolved" and gap[1] == 492,
            f"G-78 {gap if gap else '不存在'}（期望 resolved → A-492）",
            "重走第一幕：问「能刻字吗」落缺口 → 补文档挂 knowledgeGapId 发布",
        )
    )
    # 回流素材（幕 4）：A-493 对话资产停在待人洗
    expect_asset("回流对话资产 A-493", 493, "pending_review", "dialogue", "会话页挑答得好的会话点「回流登记」（手册第四幕）")
    # 必填闸素材（幕 3）：A-497 已发布 + confirm/publish 双审计行
    expect_asset("LK201 全链资产 A-497", 497, "published", "document", "登记《LK201 规格（Wikidata）》挂键盘商品→人洗确认品牌→发布（92 刀 §三点五）")
    audits = int(
        _one(
            session,
            "SELECT count(*) FROM audit_log WHERE asset_id = 497 AND action IN ('confirm', 'publish')",
        )
        or 0
    )
    checks.append(
        Check(
            "A-497 confirm+publish 双审计行",
            audits >= 2,
            f"audit 行 {audits}（期望 ≥2）",
            "A-497 若重走：PATCH 确认品牌（confirm 行）再发布（publish 行）",
        )
    )
    # 图片治理素材（幕 5/7）
    expect_asset("显示器商品图 A-501", 501, "published", "image", "上传商品图→人洗写描述→发布（94a 链路）")
    # 版本指针素材（幕 2）：A-503 线上指针在 v2（v1 无描述 → v2 有描述）
    row503 = _asset_row(session, 503)
    versions = int(
        _one(
            session,
            "SELECT count(*) FROM asset_versions WHERE asset_id = 503 AND published_at IS NOT NULL",
        )
        or 0
    )
    checks.append(
        Check(
            "版本指针资产 A-503",
            row503 is not None
            and row503[0] == "published"
            and row503[1] == "image"
            and row503[3] == 2
            and versions >= 2,
            f"A-503 {row503[0] if row503 else '缺'}/线上 v{row503[3] if row503 else '?'}，已发布版 {versions}（期望 published/线上 v2/≥2 版）",
            "A-503 开修订→人洗补图片描述→发布 v2（94b 实录形态）",
        )
    )
    # 洗帧素材（幕 6）：A-505 clip_frame 来源已发布
    row505 = _asset_row(session, 505)
    checks.append(
        Check(
            "洗帧资产 A-505",
            row505 is not None
            and row505[0] == "published"
            and row505[1] == "image"
            and row505[2] == "clip_frame",
            f"A-505 {row505 if row505 else '不存在'}（期望 published/image/clip_frame）",
            "A-264 详情「洗帧到素材库」→ 勾选候选确认登记 → 人洗描述 → 发布（94c）",
        )
    )
    return checks


def _check_demo_sessions(session: Any) -> list[Check]:
    # 手册引用的演示会话：#199 主线、#331/#332/#344（92 刀实录）、#350/#353
    # （94b/94c 实录）只要「在库且有消息」即算在位（状态会随演示自然流转）；
    # #345 的 registered+指向 A-493 是回流登记的既成事实，需整体在位。
    rows = {
        int(r[0]): (r[1], int(r[2]) if r[2] is not None else None)
        for r in session.execute(_text(
            """
            SELECT s.id, s.status, s.registered_asset_id,
                   (SELECT count(*) FROM service_messages m WHERE m.session_id = s.id)
            FROM service_sessions s WHERE s.id IN (199, 331, 332, 344, 345, 350, 353)
            """
        ))
        if r[3]  # 有消息的才进字典（空会话不算素材）
    }
    expectations = [
        (199, "主线会话（上班口径已答）"),
        (331, "92 刀缺口实录（能刻字吗→G-78）"),
        (332, "92 刀 OOV 实录（白色款/分期）"),
        (344, "再问命中实录（引 A-492）"),
        (350, "94b 双图直出实录"),
        (353, "94c 真帧实录（引 A-505）"),
    ]
    checks: list[Check] = []
    for sid, label in expectations:
        ok = sid in rows
        checks.append(
            Check(
                f"演示会话 #{sid}",
                ok,
                f"{label}：{'在库（' + rows[sid][0] + '）' if ok else '不在库或无消息'}",
                f"#{sid} 按手册对应幕重演一遍（素材链见 docs/research/real-usage-log.md）",
            )
        )
    backflow = rows.get(345)
    checks.append(
        Check(
            "回流会话 #345",
            backflow is not None and backflow[0] == "registered" and backflow[1] == 493,
            f"{backflow if backflow else '不在库'}（期望 registered → A-493）",
            "会话 #345 重新点「回流登记」（手册第四幕）",
        )
    )
    # 直播线：A-264 已发布切片 + cloud 转写候选在队
    a264 = _asset_row(session, 264)
    checks.append(
        Check(
            "直播切片源 A-264",
            a264 is not None and a264[0] == "published" and a264[1] == "video",
            f"A-264 {a264[0] if a264 else '缺'}/{a264[1] if a264 else '-'}（期望 published/video）",
            "切片页转写候选拣选真切（93 刀链路，需录像或 ASR key/替身）",
        )
    )
    cloud = int(
        _one(
            session,
            "SELECT count(*) FROM clip_candidates WHERE transcript_source = 'cloud'",
        )
        or 0
    )
    checks.append(
        Check(
            "ASR 转写候选（cloud）",
            cloud >= 3,
            f"{cloud} 条（期望 ≥3，93 刀实录三句）",
            "切片页上传录像点「自动转写」（需 ASR_API_KEY，无 key 端点 409 属诚实形态）",
        )
    )
    return checks


def run_checks(session: Any) -> list[Check]:
    return [
        *_check_digital_store(session),
        *_check_demo_assets(session),
        *_check_demo_sessions(session),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="演示库就绪检查器（只读，不重建）")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--fix-hint", action="store_true", help="缺失项附补救命令（仍不自动执行）"
    )
    args = parser.parse_args(argv)
    if not args.db:
        print("错误：需要 --db 或 DATABASE_URL", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from suite_api.db import to_sqlalchemy_url

    engine = create_engine(to_sqlalchemy_url(args.db))
    shown = re.sub(r"(://[^:/@]+:)[^@]+(@)", lambda m: m.group(1) + "***" + m.group(2), args.db)
    print(f"目标库：{shown}（就绪检查，只读不写）")
    with Session(engine) as session:
        checks = run_checks(session)
    engine.dispose()

    failed = 0
    for check in checks:
        mark = "PASS" if check.ok else "MISS"
        print(f"  [{mark}] {check.name}: {check.detail}")
        if not check.ok:
            failed += 1
            if args.fix_hint and check.hint:
                print(f"         补救: {check.hint}")
    if failed:
        print(f"\n演示库未就绪：{failed} 项缺失（{'加 --fix-hint 看补救命令' if not args.fix_hint else '见上'}；不自动重建——治理动作不自动）")
        return 1
    print("\n演示库就绪：12 幕素材全部在位。下一步可跑 demo_reset 清探针噪音（默认 dry-run）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
