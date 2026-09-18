"""经营信号扫描（第 122 刀 A，Owner 裁决：运营 Agent 重定义为信号→行动）。

旧运营模块（三步编排+mock 投放，ADR 0041）功能上与素材中心的文案生成重叠、
身份不清——Owner 亲验后看不懂。重定义：运营 Agent = 中台已有数据面的扫描器
+ 建议动作入口，每条信号回答「今天该做什么、去哪做」。行业口径（有赞智能
助手/腾讯 MAGIC）的「监控→建议→人确认」模式，我们的差异化是**信号全部
来自中台真实表**（库存/缺口/素材覆盖/stale），不造任何新数据。

四种信号：
- stock：库存告罄（=0）或偏低（≤5），行动=联系补货或下架；
- gap：知识缺口热度 Top（被问最多的未解决问题），行动=去补文档；
- coverage：有商品但无任何已发布素材覆盖，行动=去生成内容；
- stale：已发布资产超 90 天未重新验证（检索已降权），行动=去重新验证。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, KnowledgeGap, Product

STALE_DAYS = 90
LOW_STOCK_THRESHOLD = 5
TOP_GAP_LIMIT = 5


def scan_signals(db: Session) -> list[dict[str, Any]]:
    """扫描中台数据面 → 信号列表（不写库，只读——信号是视图不是任务）。"""
    signals: list[dict[str, Any]] = []

    # ---- 库存信号 ----
    products = list(db.scalars(select(Product).order_by(Product.id)))
    for product in products:
        if product.stock is None:
            continue
        if product.stock == 0:
            signals.append(_signal("stock", "out", product.id, product.name,
                                   "库存已清零", "联系补货或暂时下架"))
        elif product.stock <= LOW_STOCK_THRESHOLD:
            signals.append(_signal("stock", "low", product.id, product.name,
                                   f"库存仅剩 {product.stock} 件", "评估补货周期"))

    # ---- 缺口热度信号 ----
    gaps = list(
        db.scalars(
            select(KnowledgeGap)
            .where(KnowledgeGap.status == "open")
            .order_by(KnowledgeGap.hit_count.desc(), KnowledgeGap.id.desc())
            .limit(TOP_GAP_LIMIT)
        )
    )
    for gap in gaps:
        if gap.hit_count < 2:
            continue  # 只推被反复问的（首问的留给治理台日常处理）
        signals.append(_signal("gap", "hot", gap.id, None,
                               f"顾客被问 {gap.hit_count} 次没答上：{gap.question[:40]}",
                               "去补口径文档"))

    # ---- 素材覆盖信号 ----
    covered = set(
        db.scalars(
            select(Asset.product_id).where(
                Asset.kind.in_(("image", "video", "material")),
                Asset.status == "published",
                Asset.discarded_at.is_(None),
                Asset.product_id.is_not(None),
            )
        )
    )
    for product in products:
        if product.id not in covered:
            signals.append(_signal("coverage", "empty", product.id, product.name,
                                   "无任何已发布素材覆盖（图/视频/文案都没有）",
                                   "去生成内容"))

    # ---- Stale 信号 ----
    cutoff = datetime.now(UTC) - timedelta(days=STALE_DAYS)
    stale_assets = list(
        db.scalars(
            select(Asset).where(
                Asset.status == "published",
                Asset.discarded_at.is_(None),
                Asset.last_verified_at.is_not(None),
                Asset.last_verified_at < cutoff,
            ).order_by(Asset.last_verified_at).limit(10)
        )
    )
    for asset in stale_assets:
        days = (datetime.now(UTC) - asset.last_verified_at).days
        signals.append(_signal("stale", "expired", asset.id, asset.title,
                               f"距上次验证已 {days} 天（检索证据已降权）",
                               "去重新验证"))

    # 从新到旧排（stale 最老在前、gap 最热在前、库存最先看）
    priority = {"stock": 0, "gap": 1, "coverage": 2, "stale": 3}
    signals.sort(key=lambda s: priority.get(s["kind"], 9))
    return signals


def _signal(
    kind: str, detail: str, ref_id: int, ref_name: str | None,
    summary: str, action: str,
) -> dict[str, Any]:
    """信号形态：kind+detail → 前端图标与跳转路由；ref_id/ref_name 定位对象。"""
    routes = {
        "stock": "/platform/products",
        "gap": "/platform/assets?status=知识缺口",
        "coverage": "/material",
        "stale": f"/platform/assets/{ref_id}?verify=1",
    }
    return {
        "kind": kind,
        "detail": detail,
        "ref_id": ref_id,
        "ref_name": ref_name,
        "summary": summary,
        "action": action,
        "route": routes.get(kind, "/"),
    }
