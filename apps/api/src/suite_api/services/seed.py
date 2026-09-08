"""启动种子（0008 单店即库本身）：幂等，存在即跳过。

- 操作者 1 个：username=operator，密码来自 settings.operator_password（env
  OPERATOR_PASSWORD，默认 operator123 仅开发用，README 已写明）。
- 商品 2 个：瓶装水（食品：净含量+保质期 required）、钛钢保温杯（器皿：
  净含量+材质 required）。0019：必填集合运行时从 spec_schema 派生。
- 订单 3 单（ADR 0036）：演示店铺 mock 单，只被订单工具读（工具数据源，不是
  中台对象，与商品名无外键关系）。覆盖 已发货/运输中/已签收 三态；固定时间戳
  保证模板组装与工具条摘要确定。
"""

import logging
from datetime import UTC, datetime

import bcrypt
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from suite_api.models import Operator, Order, Product

logger = logging.getLogger(__name__)

OPERATOR_USERNAME = "operator"

SEED_PRODUCTS: list[dict] = [
    {
        "name": "瓶装水",
        "category": "食品",
        "spec_schema": {"净含量": {"required": True}, "保质期": {"required": True}},
    },
    {
        "name": "钛钢保温杯",
        "category": "器皿",
        "spec_schema": {"净含量": {"required": True}, "材质": {"required": True}},
    },
]

SEED_ORDERS: list[dict] = [
    {
        "order_no": "SO-1001",
        "status": "已发货",
        "items": [{"name": "瓶装水", "qty": 2}, {"name": "钛钢保温杯", "qty": 1}],
        "events": [
            {"at": "2026-09-05 09:12", "text": "商家已发货，包裹揽收"},
            {"at": "2026-09-05 20:40", "text": "快件已到达杭州转运中心"},
        ],
        "placed_at": datetime(2026, 9, 4, 18, 30, tzinfo=UTC),
    },
    {
        "order_no": "SO-1002",
        "status": "运输中",
        "items": [{"name": "钛钢保温杯", "qty": 1}],
        "events": [
            {"at": "2026-09-06 10:05", "text": "商家已发货，包裹揽收"},
            {"at": "2026-09-07 06:20", "text": "干线运输中，下一站上海分拨中心"},
        ],
        "placed_at": datetime(2026, 9, 5, 21, 10, tzinfo=UTC),
    },
    {
        "order_no": "SO-1003",
        "status": "已签收",
        "items": [{"name": "瓶装水", "qty": 6}],
        "events": [
            {"at": "2026-09-01 08:45", "text": "商家已发货，包裹揽收"},
            {"at": "2026-09-02 15:10", "text": "派件中，快递员 王师傅"},
            {"at": "2026-09-02 17:02", "text": "已签收，本人"},
        ],
        "placed_at": datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
    },
]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        # bcrypt 对 >72 字节密码等输入会抛错：一律按校验失败处理，不冒异常
        return False


def seed_startup_data(engine: Engine, operator_password: str) -> None:
    with Session(engine) as db:
        if db.scalar(select(Operator).where(Operator.username == OPERATOR_USERNAME)) is None:
            db.add(
                Operator(
                    username=OPERATOR_USERNAME,
                    password_hash=hash_password(operator_password),
                )
            )
            logger.info("种子操作者已创建: %s", OPERATOR_USERNAME)
        for spec in SEED_PRODUCTS:
            if db.scalar(select(Product).where(Product.name == spec["name"])) is None:
                db.add(Product(**spec))
                logger.info("种子商品已创建: %s", spec["name"])
        for spec in SEED_ORDERS:
            if db.scalar(select(Order).where(Order.order_no == spec["order_no"])) is None:
                db.add(Order(**spec))
                logger.info("种子订单已创建: %s", spec["order_no"])
        db.commit()
