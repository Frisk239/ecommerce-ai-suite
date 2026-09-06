"""启动种子（0008 单店即库本身）：幂等，存在即跳过。

- 操作者 1 个：username=operator，密码来自 settings.operator_password（env
  OPERATOR_PASSWORD，默认 operator123 仅开发用，README 已写明）。
- 商品 2 个：瓶装水（食品：净含量+保质期 required）、钛钢保温杯（器皿：
  净含量+材质 required）。0019：必填集合运行时从 spec_schema 派生。
"""

import logging

import bcrypt
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from suite_api.models import Operator, Product

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
        db.commit()
