"""反馈分诊的资产集合（第 40 刀 ADR 0044 §四；第 43 刀加固；审计刀 8 下沉服务层）。

原是 `routes/customer.py` 里的私有纯函数，第 43 刀的仪表聚合也用它——于是出现了
全仓唯一的 route→route 导入（`routes/stats.py` → `routes/customer.py`）。第 42 刀
刚把当时那条同类坏味道消掉，这里按同一纪律下沉到服务层：路由只做 HTTP，纯函数归
服务。
"""

from typing import Any


def triage_asset_ids(citations: list[dict[str, Any]]) -> list[int]:
    """负反馈分诊的资产集合（纯函数便于单测）：逐 citation 收集 asset_id 去重升序。

    防御式解析（第 43 刀加固：读路径复用后不允许坏行 500）：只收 dict 且
    ``asset_id`` 为 int 的条目——键缺失、``asset_id: null``、字符串 id 都跳过
    （``int(None)`` 会抛 TypeError；bool 是 int 子类，防御性排除，同 lineage 口径）。
    """
    asset_ids: set[int] = set()
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        asset_id = citation.get("asset_id")
        if isinstance(asset_id, int) and not isinstance(asset_id, bool):
            asset_ids.add(asset_id)
    return sorted(asset_ids)
