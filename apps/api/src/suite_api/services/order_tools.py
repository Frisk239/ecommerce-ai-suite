"""订单工具（ADR 0036）：只读 get_order_status + 模板组装回答。

- 分派依据是纯函数 ``find_order_no``：问题文本命中 ``SO-\\d+``（大小写不敏感，
  归一为大写）即走工具路径，跳过检索；不命中零成本，既有路径不动。
- ``get_order_status`` 只读 orders 表（工具数据源，不是中台对象）：命中返回
  结构化订单，查无 ``{found: False}``；DB 异常捕获为 ``{error: True}`` 不向
  上炸——转人工由引擎的 handoff 分支表达，失败不拿检索顶（ADR 0018）。
- 回答用确定性模板组装（v1 不调 LLM，0036：结构化数据以最诚实）；工具条
  一行摘要 ``summarize_tool_result`` 与引用芯片（青底）语义分离（UX-NOTES §6）。
"""

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from suite_api.models import Order

# 吞异常转 handoff 的对外行为不变（0018：失败不拿检索顶），但线上必须能看到
# 原因——审计刀 3 P1#6：捕获处 logger.exception（原文只进服务端日志）
logger = logging.getLogger(__name__)

# 订单号=知识型凭证（0021 顾客无账号）：单号由提问者自己给出，无内部敏感字段
ORDER_NO_PATTERN = re.compile(r"SO-\d+", re.IGNORECASE)

_NOT_FOUND_CONTENT_TPL = "订单 {order_no} 未找到，已转人工，请人工核实单号。"
_ERROR_CONTENT = "订单查询失败，已转人工。"

# ---------- 订单状态合法值集（第 44 刀）----------
# 订单是工具数据源、不是中台对象（0002），但「状态」是它唯一的对外语义：确认退货
# 若不迁移状态，追问一句「退货进度怎么样」就穿帮（纸糊#3）。退货因此是状态机的
# 正式成员（调研：commerce-agents 官方 OrderStatus 含 return_initiated/refunded）。
ORDER_STATUS_SHIPPED = "已发货"
ORDER_STATUS_IN_TRANSIT = "运输中"
ORDER_STATUS_SIGNED = "已签收"
ORDER_STATUS_RETURNING = "退货中"
ORDER_STATUS_REFUNDED = "已退款"

# 合法全集：任何新写入的状态都必须落在这里（DB CHECK 未加——订单不是中台对象，
# 演示规模下代码收口 + 钉测足够，见第 44 刀 closeout 的记债）。
ORDER_STATUSES: frozenset[str] = frozenset(
    {
        ORDER_STATUS_SHIPPED,
        ORDER_STATUS_IN_TRANSIT,
        ORDER_STATUS_SIGNED,
        ORDER_STATUS_RETURNING,
        ORDER_STATUS_REFUNDED,
    }
)

# 可发起退货的前置态：退货中/已退款不许再来一次——状态机不回退，否则一张已退款的
# 单子还能被「确认退货」改回退货中（阶段一只看时间窗，这条闸是它缺的另一半）。
RETURNABLE_STATUSES: frozenset[str] = frozenset(
    {ORDER_STATUS_SHIPPED, ORDER_STATUS_IN_TRANSIT, ORDER_STATUS_SIGNED}
)


def find_order_no(text: str) -> str | None:
    """提取首个订单号并归一大写（seed/查询口径同为 ``SO-\\d+`` 大写）。"""
    match = ORDER_NO_PATTERN.search(text)
    return match.group(0).upper() if match else None


def get_order_status(db: Session, order_no: str) -> dict[str, Any]:
    """只读查单。命中 {found: True, order_no, status, items, events}；
    查无 {found: False}；DB 异常 {error: True}（吞异常并尽力回滚，调用方
    仍可继续落 handoff 消息）。"""
    try:
        order = db.scalar(select(Order).where(Order.order_no == order_no))
    except SQLAlchemyError:
        logger.exception("订单工具查询订单失败: order_no=%s", order_no)
        # 尽力恢复会话可用（连接失效等），回滚本身再失败就不管了
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("订单工具回滚失败（吞异常后会话可能不可用）")
        return {"error": True}
    if order is None:
        return {"found": False}
    return {
        "found": True,
        "order_no": order.order_no,
        "status": order.status,
        "items": list(order.items),
        "events": list(order.events),
    }


def summarize_tool_result(result: dict[str, Any]) -> str:
    """工具条一行结果摘要（灰底 mono：get_order_status(SO-1001) → 此处）。"""
    if result.get("error"):
        return "查询失败"
    if not result.get("found"):
        return "未找到"
    return f"{result['status']} · {len(result['events'])} 个物流事件"


def render_order_answer(result: dict[str, Any]) -> str:
    """命中订单的确定性模板组装（0036：v1 不调 LLM，citations 恒空）。"""
    lines = [f"订单 {result['order_no']} 当前状态：{result['status']}。"]
    item_parts = [f"{item['name']} ×{item['qty']}" for item in result["items"] if item.get("name")]
    if item_parts:
        lines.append("商品：" + "、".join(item_parts) + "。")
    events = result["events"]
    if events:
        lines.append("物流轨迹：")
        lines.extend(f"- {event['at']} {event['text']}" for event in events)
    return "\n".join(lines)


def render_handoff_content(order_no: str, result: dict[str, Any]) -> str:
    """查无/故障的交接摘要文本（转人工 v1 只是消息种类，无坐席队列）。"""
    if result.get("error"):
        return _ERROR_CONTENT
    return _NOT_FOUND_CONTENT_TPL.format(order_no=order_no)
