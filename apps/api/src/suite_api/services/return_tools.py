"""退货资格工具与两阶段写（第 40 刀，ADR 0044 §一）。

两段语义（ADR 0044 锁定，唯一语义源）：

- 阶段一 资格查询（只读）：``check_return_eligibility`` 在模型可提议注册表内
  （agent_tools）——order_no 校验、窗期判定**在代码不在 prompt**（ADR 0044：
  越狱的模型也发不出非法创建）：orders.placed_at 起 15 天内 eligible（v1 无
  签收概念，用下单时间口径，对齐演示库退货政策资产）；eligible 时签发
  confirmation_token 返回。查无 ``{found: False}``、DB 异常 ``{error: True}``
  ——与订单工具同一吞异常转 handoff 口径（0018）。
- 阶段二 创建（写，人确认闸）：``create_return`` **不在注册表**——写工具不
  在模型可提议集（0043「模型提议、代码授权」的授权语义延伸），唯一入口是
  操作者确认端点（routes/service.confirm_return）携 token 调 ``confirm_return``：
  再验签名/过期/资格重查未变 -> orders.events 追加一条确认事件（mock 执行，
  不改表结构）。

confirmation_token（无状态 HMAC，spec 工程裁决）：HMAC-SHA256 密钥对
``order_no:eligible:exp`` 取前 32 hex + exp（Unix 秒十进制）。资格结果哈希
绑定在签名里——重查出窗旧 token 自然失效。10 分钟有效。密钥 env
``RETURN_TOKEN_SECRET`` 优先，缺省从 settings.session_secret 派生——
**演示语境钉死**（单店内部演示系统，无资金动作；生产部署必须显式配 env）。
"""

import hashlib
import hmac
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from suite_api.models import Order

logger = logging.getLogger(__name__)

# 窗期规则（在代码不在 prompt，ADR 0044 §一）：下单后 15 天内可退
RETURN_WINDOW_DAYS = 15
# 确认令牌有效期（秒）：会话挂起待确认卡片的合理等待量级
TOKEN_TTL_SECONDS = 600
# 确认事件的固定文案（也是确认幂等判据：events 里已有同文案=已确认过）
CONFIRM_EVENT_TEXT = "退货申请已确认（操作者确认两阶段）"
# 签名段长度（hex 字符）：token = 签名 + exp 十进制串
_SIG_CHARS = 32

# 退货意图（v1 词表，快路径分派用）：单号问句含退货词即走资格查询。
RETURN_INTENT_RE = re.compile(r"退货|退换|退了")
# 但问进度/轨迹的（「退货进度」）不是要发起退货——回订单工具看事件时间轴。
_RETURN_PROGRESS_RE = re.compile(r"进度|轨迹|物流|事件|到哪|状态|查询")
# 审计刀 7 P1#3：问政策/条件/流程/支持/规则的是知识问答（检索可答），
# 不是发起退货——走快路径会吞掉退货政策资产并制造虚假「退货申请已生成」。
_RETURN_POLICY_RE = re.compile(r"政策|条件|流程|支持|规则|怎么退|如何退|可以退吗|能退吗")


def has_return_intent(text: str) -> bool:
    """退货发起意图（纯函数便于单测）：含退货词、非问进度/轨迹、非问政策。

    「SO-1001 我想退货」-> True（走资格查询）；「SO-1001 退货进度」-> False
    （走订单状态工具看事件——确认后的事件由此对顾客可见，演示闭环）；
    「SO-1001 退货政策是什么」-> False（走检索/提议，政策资产可答）。"""
    if _RETURN_PROGRESS_RE.search(text) or _RETURN_POLICY_RE.search(text):
        return False
    return bool(RETURN_INTENT_RE.search(text))


def return_secret() -> bytes:
    """确认令牌密钥：env RETURN_TOKEN_SECRET 优先；缺省从 settings.session_secret
    派生（演示语境钉死，见模块 docstring）。调用时读取（非 import 时），测试
    可 monkeypatch.setenv 精确控制。"""
    env = os.environ.get("RETURN_TOKEN_SECRET", "")
    if env:
        return env.encode()
    from suite_api.settings import get_settings

    return f"return-token:{get_settings().session_secret}".encode()


def make_token(order_no: str, eligible: bool, *, now: datetime | None = None) -> str:
    """签发确认令牌：``HMAC(order_no:eligible:exp)[:32] + exp``。

    now 可注入（单测钉过期态）；exp = now + TOKEN_TTL_SECONDS 取整秒。
    eligible 进签名材料：资格变了旧 token 自动失效（阶段二会重查资格，签名
    绑定是第一道闸）。"""
    moment = now or datetime.now(UTC)
    exp = int(moment.timestamp()) + TOKEN_TTL_SECONDS
    sig = hmac.new(
        return_secret(), f"{order_no}:{eligible}:{exp}".encode(), hashlib.sha256
    ).hexdigest()[:_SIG_CHARS]
    return f"{sig}{exp}"


def verify_token(token: str, order_no: str, eligible: bool, *, now: datetime | None = None) -> bool:
    """验签三关：形状（长度/解析）-> 签名（compare_digest 恒定时间）->
    exp 未过期。篡改/过期/资格不符/密钥不符一律 False。now 可注入。"""
    if len(token) <= _SIG_CHARS:
        return False
    sig, raw_exp = token[:_SIG_CHARS], token[_SIG_CHARS:]
    try:
        exp = int(raw_exp)
    except ValueError:
        return False
    expected = hmac.new(
        return_secret(), f"{order_no}:{eligible}:{exp}".encode(), hashlib.sha256
    ).hexdigest()[:_SIG_CHARS]
    if not hmac.compare_digest(sig, expected):
        return False
    return exp >= int((now or datetime.now(UTC)).timestamp())


def is_within_window(placed_at: datetime, *, now: datetime | None = None) -> bool:
    """窗期判定（纯函数便于单测）：下单后 RETURN_WINDOW_DAYS 天内 True。
    placed_at 带时区（模型 DateTime(timezone=True)），now 默认 UTC。"""
    moment = now or datetime.now(UTC)
    return moment - placed_at <= timedelta(days=RETURN_WINDOW_DAYS)


def check_return_eligibility(db: Session, order_no: str) -> dict[str, Any]:
    """阶段一（只读）：命中返回 ``{found, order_no, eligible, reason[
    , confirmation_token]}``；查无 ``{found: False}``；DB 异常 ``{error: True}``
    （吞异常尽力回滚，与订单工具同口径——转人工由引擎 handoff 分支表达）。"""
    try:
        order = db.scalar(select(Order).where(Order.order_no == order_no))
    except SQLAlchemyError:
        logger.exception("退货资格查询订单失败: order_no=%s", order_no)
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("退货资格查询回滚失败（吞异常后会话可能不可用）")
        return {"error": True}
    if order is None:
        return {"found": False}
    eligible = is_within_window(order.placed_at)
    result: dict[str, Any] = {
        "found": True,
        "order_no": order.order_no,
        "eligible": eligible,
        "reason": (
            f"下单 {RETURN_WINDOW_DAYS} 天内，符合退货条件"
            if eligible
            else f"下单已超过 {RETURN_WINDOW_DAYS} 天，不在退货窗内"
        ),
    }
    if eligible:
        # 阶段一凭证：阶段二（确认端点）凭它执行创建；模型拿不到创建权
        result["confirmation_token"] = make_token(order.order_no, True)
    return result


def summarize_eligibility(result: dict[str, Any]) -> str:
    """工具条一行结果摘要（灰底 mono）。eligible 带「待确认 token=…」——
    ServicePage 检测该串渲染「确认退货」按钮（操作者通道；顾客通道无确认面，
    只是文本留档）。"""
    if result.get("error"):
        return "查询失败"
    if not result.get("found"):
        return "未找到"
    if result.get("eligible"):
        return f"可退货 · 待确认 token={result.get('confirmation_token', '')}"
    return f"不可退货 · {result.get('reason', '')}"


def render_eligibility_answer(result: dict[str, Any]) -> str:
    """资格结果到顾客可见回答的确定性模板（v1 不调 LLM：资格判定是结构化
    事实，模板最诚实；token 只进工具条不进顾客文本）。eligible 用 ADR 0044
    「已生成退货申请，等待客服确认」口径。"""
    order_no = str(result.get("order_no", ""))
    if result.get("eligible"):
        return f"订单 {order_no} 在 {RETURN_WINDOW_DAYS} 天退货窗内，退货申请已生成，等待客服确认。"
    return f"订单 {order_no} 暂不符合退货条件：{result.get('reason', '')}。"


def confirm_return(db: Session, order_no: str, token: str) -> dict[str, Any]:
    """阶段二（写，人确认闸）：验签 + 订单重查资格未变 -> orders.events 追加
    确认事件（list append，不改表结构）。

    返回 ``{ok: True, order_no, events}``；失败 ``{ok: False, reason}``——
    reason ∈ invalid_token（验签/过期/篡改）/ not_found（订单消失）/
    window_changed（重查已出窗）/ duplicate（已确认过，幂等）。commit 归调用
    方（确认端点把事件追加与 create_return 轨迹消息并进同一事务）。"""
    if not verify_token(token, order_no, True):
        return {"ok": False, "reason": "invalid_token"}
    try:
        order = db.scalar(select(Order).where(Order.order_no == order_no))
    except SQLAlchemyError:
        logger.exception("退货确认查询订单失败: order_no=%s", order_no)
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("退货确认回滚失败（吞异常后会话可能不可用）")
        return {"ok": False, "reason": "not_found"}
    if order is None:
        return {"ok": False, "reason": "not_found"}
    if not is_within_window(order.placed_at):
        return {"ok": False, "reason": "window_changed"}
    events = list(order.events)
    if any(event.get("text") == CONFIRM_EVENT_TEXT for event in events):
        return {"ok": False, "reason": "duplicate"}
    events.append({"at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"), "text": CONFIRM_EVENT_TEXT})
    # JSONB 整体重新赋值（而非原地 append）保证变更被追踪并 flush
    order.events = events
    return {"ok": True, "order_no": order.order_no, "events": list(order.events)}
