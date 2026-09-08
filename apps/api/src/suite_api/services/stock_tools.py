"""库存工具（ADR 0037）：只读 get_stock + 模板组装回答（0036 同模式第二实例）。

- 分派词表在代码：``STOCK_KEYWORD_PATTERN`` 命中才进库存路径（订单号优先，
  第 13 刀在先）；词表外问题走既有检索路径零漂移。误伤代价低：商品未命中
  即转人工，与无证据拒答转人工同归宿（0037）。
- ``match_product`` 纯代码商品匹配：商品名与问题最长公共子串（按字符）≥2 字
  命中，多命中取最长 LCS、平手取 id 小——不做 NLP/LLM/分词库/别名表。
- ``get_stock`` 只读 products.stock 列（工具数据源，不是中台对象，0002 不升
  格）：返回 ``{found, product_name, stock}``，``stock=None`` 即未设置；DB 异常
  捕获为 ``{error: True}`` 不向上炸——转人工由引擎 handoff 分支表达，失败不拿
  检索顶（0018，吞异常+尽力回滚同 order 模式）。
- 回答用确定性模板（v1 不调 LLM）：stock>0「有货」含件数；stock==0「暂时无货」
  是事实数据不是失败（正常 answer）；NULL/未命中/异常 → kind="handoff"。
  工具条一行摘要 ``summarize_stock_result`` 与引用芯片语义分离（UX-NOTES §6）。
"""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from suite_api.models import Product

# 词表分派（0037）：规格类词（净含量/保质期/材质）刻意不在列——规格问题必须
# 零漂移走检索；词条 _Avoid_「用规格文档回答有没有货」自此闭环
STOCK_KEYWORD_PATTERN = re.compile("有货|没货|无货|缺货|库存|现货|剩")

# 商品名与问题最长公共子串的最小命中长度（中文字符计）
_MATCH_MIN_LCS = 2

_IN_STOCK_TPL = "{product_name}有货，当前库存 {stock} 件。"
_OUT_OF_STOCK_TPL = "{product_name}暂时无货。"
_UNSET_HANDOFF_TPL = "{product_name}库存未设置，已转人工。"
_NO_PRODUCT_HANDOFF = "没有找到对应商品，已转人工。"
_ERROR_HANDOFF = "库存查询失败，已转人工。"


def _lcs_len(a: str, b: str) -> int:
    """两串最长公共子串长度（按字符，滚动数组 DP；商品名/问题文本都短，够用）。"""
    best = 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                value = prev[j - 1] + 1
                cur[j] = value
                if value > best:
                    best = value
        prev = cur
    return best


def match_product(question: str, products: list[Product]) -> Product | None:
    """纯函数商品匹配：LCS ≥2 字命中；多命中取最长 LCS，平手取 id 小。"""
    winner: Product | None = None
    best_key: tuple[int, int] | None = None
    for product in products:
        lcs = _lcs_len(question, product.name)
        if lcs < _MATCH_MIN_LCS:
            continue
        # 负长度在前（越长越优），同长按 id 升序（无 id 的内存对象按 0 计）
        key = (-lcs, product.id if product.id is not None else 0)
        if best_key is None or key < best_key:
            winner, best_key = product, key
    return winner


def get_stock(db: Session, product: Product) -> dict[str, Any]:
    """只读查库存。命中 {found: True, product_name, stock}（stock 可为 None=
    未设置）；DB 异常 {error: True}（吞异常并尽力回滚——属性访问可能触发惰性
    加载，调用方仍可继续落 handoff 消息，同 order 模式）。"""
    try:
        name = product.name
        stock = product.stock
    except SQLAlchemyError:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return {"error": True}
    return {"found": True, "product_name": name, "stock": stock}


def query_stock(db: Session, question: str) -> dict[str, Any]:
    """库存工具入口：列商品 -> match_product -> get_stock（引擎分派后单点调用，
    单测在此打桩）。商品未命中 {found: False}；DB 异常（列查询或读取失败）
    {error: True}；命中 {found: True, product_name, stock}。"""
    try:
        products = list(db.scalars(select(Product).order_by(Product.id)))
    except SQLAlchemyError:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return {"error": True}
    product = match_product(question, products)
    if product is None:
        return {"found": False}
    return get_stock(db, product)


def summarize_stock_result(result: dict[str, Any]) -> str:
    """工具条一行结果摘要（灰底 mono：get_stock(钛钢保温杯) → 此处）。"""
    if result.get("error"):
        return "查询失败"
    if not result.get("found"):
        return "未找到商品"
    stock = result["stock"]
    if stock is None:
        return "未设置"
    if stock > 0:
        return f"有货 · {stock} 件"
    return "暂时无货"


def render_stock_answer(result: dict[str, Any]) -> str:
    """事实分支模板（0037：stock==0 是事实数据不是失败，正常回答）。"""
    stock = result["stock"]
    if stock is not None and stock > 0:
        return _IN_STOCK_TPL.format(product_name=result["product_name"], stock=stock)
    return _OUT_OF_STOCK_TPL.format(product_name=result["product_name"])


def render_stock_handoff_content(result: dict[str, Any]) -> str:
    """未设置/未命中/故障的交接摘要文本（转人工 v1 只是消息种类，无坐席队列）。"""
    if result.get("error"):
        return _ERROR_HANDOFF
    if not result.get("found"):
        return _NO_PRODUCT_HANDOFF
    return _UNSET_HANDOFF_TPL.format(product_name=result["product_name"])
