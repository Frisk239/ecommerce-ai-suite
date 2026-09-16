"""类目 → 规格字段模板（0019：必填集合运行时从 spec_schema 派生）。

种子商品、Wikidata 灌库、WANDS 承载商品共用这一份，避免脚本与中台各写一份空 schema。
未知类目返回空 dict（不发明字段）。机洗抽不到的必填键走弃权，人洗补填后才能发布。
"""

from typing import Any

# 食品/器皿与 seed.py 历史口径一致；消费电子与家具是数据刀后真实目录用的类目。
SCHEMA_BY_CATEGORY: dict[str, dict[str, dict[str, bool]]] = {
    "食品": {"净含量": {"required": True}, "保质期": {"required": True}},
    "器皿": {"净含量": {"required": True}, "材质": {"required": True}},
    "智能手机": {"品牌": {"required": True}, "存储容量": {"required": False}},
    "笔记本电脑": {"品牌": {"required": True}, "内存": {"required": False}},
    "平板电脑": {"品牌": {"required": True}},
    "电视机": {"屏幕尺寸": {"required": True}},
    "洗衣机": {"容量": {"required": True}},
    "图书": {"作者": {"required": True}},
    "家具": {"材质": {"required": True}},
    # 数码外设店主力类目（第 90 刀）：品牌是 Wikidata P176 抽出的必填锚，
    # 其余字段（显示器高/宽、耳机上市年份）覆盖稀疏（实测 22–28%），不设必填。
    # 第 94a 刀（审计 18 归位）：四类加 ``图片``（Wikidata P18 有图商品的回填位）
    # 与 ``官网``（P856）两个可选字段——只在本表登记字段位（模板条目），本刀
    # **不做** Wikidata 拉图（Pexels 辅轨与批量拉图归使用剧本；图片资产靠运营
    # 上传通道进中台）。爬虫/导入脚本按这两个键回填时不再落进「未知字段弃权」。
    "键盘": {"品牌": {"required": True}, "图片": {"required": False}, "官网": {"required": False}},
    "鼠标": {"品牌": {"required": True}, "图片": {"required": False}, "官网": {"required": False}},
    "显示器": {
        "品牌": {"required": True},
        "高度": {"required": False},
        "宽度": {"required": False},
        "图片": {"required": False},
        "官网": {"required": False},
    },
    "耳机": {
        "品牌": {"required": True},
        "上市年份": {"required": False},
        "图片": {"required": False},
        "官网": {"required": False},
    },
}


def schema_for_category(category: str) -> dict[str, Any]:
    """返回该类目 spec_schema 的浅拷贝；未知类目 {}。"""
    schema = SCHEMA_BY_CATEGORY.get(category) or {}
    return {name: dict(rule) for name, rule in schema.items()}
