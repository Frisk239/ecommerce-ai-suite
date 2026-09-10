"""启动种子（0008 单店即库本身）：幂等，存在即跳过。

- 操作者 1 个：username=operator，密码来自 settings.operator_password（env
  OPERATOR_PASSWORD，默认 operator123 仅开发用，README 已写明）。
- 商品 2 个：瓶装水（食品：净含量+保质期 required）、钛钢保温杯（器皿：
  净含量+材质 required）。0019：必填集合运行时从 spec_schema 派生。
  0037：stock 是商品字段（mock 值，只被库存工具读）——保温杯 42（有货演示）、
  瓶装水 0（无货演示）；已存在的行仅当 stock IS NULL 时回填，不覆盖手改值。
  第 41 刀：price_cents 是商品字段（类目基准演示价：瓶装水 300 分/保温杯
  12900 分，出处=本文件 DEMO 基准，非真实售价；仅 NULL 回填，不覆盖手改价，
  currency 缺省 CNY 由迁移 server_default 回填）。
- 订单 3 单（ADR 0036）：演示店铺 mock 单，只被订单工具读（工具数据源，不是
  中台对象，与商品名无外键关系）。覆盖 已发货/运输中/已签收 三态；固定时间戳
  保证模板组装与工具条摘要确定。
- 切片候选 4 条（ADR 0014/0039，第 18 刀）：直播切片模块自有的 mock——时间码
  边界/ASR 转写/源录像名称，保温杯与瓶装水各有覆盖；转写含可检索关键词
  （钛钢内胆/316不锈钢/整箱24瓶）。业务键 = (timecode_start, transcript)，
  存在即跳过。
"""

import logging
from datetime import UTC, datetime

import bcrypt
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from suite_api.models import ClipCandidate, Operator, Order, Product
from suite_api.services.category_schema import schema_for_category
from suite_api.services.clips import PENDING as CLIP_PENDING

logger = logging.getLogger(__name__)

OPERATOR_USERNAME = "operator"

SEED_PRODUCTS: list[dict] = [
    {
        "name": "瓶装水",
        "category": "食品",
        "spec_schema": {"净含量": {"required": True}, "保质期": {"required": True}},
        # 0037：0 演示「暂时无货」事实回答路径
        "stock": 0,
        # 第 41 刀：类目基准演示价 300 分（3 元）——出处=本文件 DEMO 基准
        # （mock 演示数据，非真实售价；与 stock 同口径：仅 NULL 回填）。
        "price_cents": 300,
    },
    {
        "name": "钛钢保温杯",
        "category": "器皿",
        "spec_schema": {"净含量": {"required": True}, "材质": {"required": True}},
        # 0037：42 演示「有货 · 42 件」
        "stock": 42,
        # 第 41 刀：类目基准演示价 12900 分（129 元）——出处同上。
        "price_cents": 12900,
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


SEED_CLIPS: list[dict] = [
    {
        "product_name": "钛钢保温杯",
        "timecode_start": "00:02:14",
        "timecode_end": "00:02:52",
        "transcript": (
            "现场实测保温：早上九点灌的95度热水，现在下午三点，温度计显示还有63度。"
            "钛钢内胆一体成型，没有焊缝，泡柠檬水也不怕腐蚀。"
        ),
        "source_video_label": "2026-09-04 「钛钢保温杯 × 饮用水」专场·录像 24 分钟",
    },
    {
        "product_name": "钛钢保温杯",
        "timecode_start": "00:11:26",
        "timecode_end": "00:12:10",
        "transcript": (
            "有家人问内胆牌号：外层是钛钢，内胆为316不锈钢，食品级接触材质，"
            "长期泡枸杞茶、柠檬水都放心。"
        ),
        "source_video_label": "2026-09-04 「钛钢保温杯 × 饮用水」专场·录像 24 分钟",
    },
    {
        "product_name": "瓶装水",
        "timecode_start": "00:14:05",
        "timecode_end": "00:14:38",
        "transcript": (
            "镜头带到水源地：海拔3800米天然低钠淡矿，直接灌装零添加。"
            "今天整箱24瓶带走，比单瓶买划算。"
        ),
        "source_video_label": "2026-09-04 「钛钢保温杯 × 饮用水」专场·录像 24 分钟",
    },
    {
        "product_name": "瓶装水",
        "timecode_start": "00:16:48",
        "timecode_end": "00:17:35",
        "transcript": (
            "现场踩给各位看：瓶身轻但抗压，整箱堆放不变形。保质期12个月，常温避光存放就行。"
        ),
        "source_video_label": "2026-09-04 「钛钢保温杯 × 饮用水」专场·录像 24 分钟",
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
            existing = db.scalar(select(Product).where(Product.name == spec["name"]))
            if existing is None:
                db.add(Product(**spec))
                logger.info("种子商品已创建: %s", spec["name"])
            else:
                if existing.stock is None:
                    # 0037 回填：迁移只加列（存量行 NULL=未设置），mock 值在这灌；
                    # 仅当 NULL 时写——已有值（含演示中手改成 0/其他）不覆盖
                    existing.stock = spec["stock"]
                    logger.info("种子商品库存已回填: %s=%s", spec["name"], spec["stock"])
                if existing.price_cents is None and spec.get("price_cents") is not None:
                    # 第 41 刀回填：同 0037 纪律——迁移只加列（存量行 NULL=
                    # 未定价），类目基准演示价在这灌；仅当 NULL 时写，已有价
                    # （含手改价）不覆盖
                    existing.price_cents = spec["price_cents"]
                    logger.info("种子商品演示价已回填: %s=%s", spec["name"], spec["price_cents"])
                if not existing.spec_schema:
                    existing.spec_schema = (
                        schema_for_category(existing.category) or spec["spec_schema"]
                    )
                    logger.info("种子商品规格模板已回填: %s", spec["name"])
        for spec in SEED_ORDERS:
            if db.scalar(select(Order).where(Order.order_no == spec["order_no"])) is None:
                db.add(Order(**spec))
                logger.info("种子订单已创建: %s", spec["order_no"])
        for spec in SEED_CLIPS:
            # 业务键 = (timecode_start, transcript)：存在即跳过（幂等）。商品按名
            # 取——products 在前面同函数已灌，select 触发 autoflush 拿得到新行主键。
            product = db.scalar(select(Product).where(Product.name == spec["product_name"]))
            if product is None:  # pragma: no cover - 商品恒先于候选灌入
                continue
            existing = db.scalar(
                select(ClipCandidate).where(
                    ClipCandidate.timecode_start == spec["timecode_start"],
                    ClipCandidate.transcript == spec["transcript"],
                )
            )
            if existing is None:
                db.add(
                    ClipCandidate(
                        product_id=product.id,
                        status=CLIP_PENDING,
                        timecode_start=spec["timecode_start"],
                        timecode_end=spec["timecode_end"],
                        transcript=spec["transcript"],
                        source_video_label=spec["source_video_label"],
                    )
                )
                logger.info("种子切片候选已创建: %s", spec["timecode_start"])
        db.commit()
