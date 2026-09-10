"""目录回落（第 41 刀，ADR 0045）：retrieve 无命中时的商品列举/报价。

- 触发（调用方在 retrieve 返回 [] 后调 ``try_catalog_answer``，有命中永不
  进本模块）：目录意图才回落——列举（卖什么/有什么/目录/在售…）或报价
  （多少钱/价格/售价/…/多少）。命中（retrieve 非空）+ 有价走正常 RAG，
  与本模块无关。
- 证据语义：工具式模板（不调 LLM、citations 恒空、kind=answer）——商品行
  是工具数据源（与 stock/orders 同口径：商品不是中台对象，0002），不是
  引用（不碰 0007 引用锚）。
- miss 归宿：无匹配/无价/空店返回 None——调用方走既有拒答+转人工+落缺口
  （去补=上新商品/改价：回落读实时行价，同问可答）。
- 零回归闸（既有拒答用例钉死）：
  - 列举意图须「纯」：去掉意图词与目录语境词后不得剩实质内容——
    「会员日有什么优惠」是优惠问不是目录问，照旧拒答；
  - 裸「多少」须无规格词：规格字段名（类目模板键同源）与常见规格词在场
    时是规格问（「净含量是多少」「还剩多少毫升」），不抢；
  - 显式要真人（转人工/真人）不抢，留第 42 刀。
- 商品匹配复用 stock_tools.match_product（词表+LCS≥2 字，0037 同口径）。
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Product
from suite_api.services.category_schema import SCHEMA_BY_CATEGORY
from suite_api.services.stock_tools import match_product

# 工具式模板的轨迹名（SSE tool 事件与消息 tool 列同形状 {name, arg, result}，
# 与 get_order_status/get_stock 同为「已发生的只读动作留档」，不是证据引用）。
TOOL_NAME = "catalog"
# 列举形态的轨迹参数（无商品指向，恒为目录本身）。
CATALOG_LISTING_ARG = "目录"
# 列举上限：前 N 件 + 总数（防大店刷屏，N 为工程初值）。
MAX_LISTED = 20

# 列举意图（长串优先，提纯时按实际命中 span 剔除；口语形态「卖啥/卖啥的/
# 卖什么的/有目录吗/有哪些啊」——交接审查揪出原表漏掉主打痛点句式）。
LISTING_RE = re.compile("卖什么|卖啥|有什么|有啥|都卖哪|卖哪些|有哪些|目录|在售")
# 报价意图（显式价格词；裸「多少」另走规格闸）。
PRICE_RE = re.compile("多少钱|价格|售价|定价|报价|收费|贵不贵|便宜|花多少")
# 裸「多少」（ verdict 触发词之一）：规格词在场时是规格问，不触发。
_BARE_MUCH_RE = re.compile("多少")
# 政策词闸（交接审查实修：退货运费/优惠规则/发票税费这类政策问即使带价格词
# 也不是报价问——无证据库下答售价=答非所问，照旧拒答留缺口走治理去补）。
_POLICY_RE = re.compile("退货|退换|退了|退款|运费|邮费|物流|快递|优惠|折扣|发票|收据|税费|保价|赔偿")
# 显式要真人不抢（留第 42 刀）：转人工仍走既有拒答+转人工。
_HUMAN_RE = re.compile("转人工|真人|人工客服|找人工")

# 规格词闸：类目模板字段键（单源：与 category_schema 同源，类目加字段自动
# 生效——注意只要字段键，类目名本身不是规格词）+ 常见规格/单位词兜底。
_SPEC_WORDS: frozenset[str] = frozenset(
    field for schema in SCHEMA_BY_CATEGORY.values() for field in schema
) | {
    "规格",
    "参数",
    "毫升",
    "毫安",
    "保修",
    "刻度",
    "批号",
    "型号",
    "尺码",
    "成分",
    "配料",
    "产地",
    "重量",
    "体积",
    "颜色",
    "包装",
    "口味",
    "度数",
    "尺寸",
}
_SPEC_WORD_RE = re.compile("|".join(sorted(_SPEC_WORDS, key=len, reverse=True)))

# 目录语境词：提纯时可剔除（意图词本身按命中 span 剔除，不在此列）。
# 语气词（的吗呢啊嘛呀吧呗哦噢）单独一段：目录问句的天然尾巴（交接审查
# 揪出「卖什么的？」「有目录吗」「有哪些啊」被判不纯——恰是主打痛点句式）。
_TRIVIAL_RE = re.compile(
    "你们|咱们|本店|小店|店铺|店里|店|铺|请问|一下|当前|现在|目前|商品|"
    "东西|产品|宝贝|出售|在卖|卖|有|哪些|什么|列表|清单|全|部|都|和|与|"
    "[的吗呢啊嘛呀吧呗哦噢]|"
    "、|，|？|\\?|！|!|。| |　"
)

CatalogIntent = Literal["listing", "price"]


def catalog_intent(question: str) -> CatalogIntent | None:
    """纯函数意图判定（无 IO，便于单测）：listing / price / None（不回落）。

    顺序即优先级：显式真人 > 政策词闸 > 纯列举 > 显式价格词 > 裸多少
    （须无规格词）。不纯的列举（如会员日优惠/卖点问）与带政策词的报价
    （退货运费谁付多少钱）直接 None——照旧拒答留缺口走治理。
    """
    if _HUMAN_RE.search(question):
        return None
    if _POLICY_RE.search(question):
        return None
    listing_match = LISTING_RE.search(question)
    if listing_match is not None:
        rest = question[: listing_match.start()] + question[listing_match.end() :]
        if _TRIVIAL_RE.sub("", rest) == "":
            return "listing"
        return None
    if PRICE_RE.search(question):
        return "price"
    if _BARE_MUCH_RE.search(question) and _SPEC_WORD_RE.search(question) is None:
        return "price"
    return None


def format_price(price_cents: int | None, currency: str | None) -> str:
    """行价展示：分→元（去尾零）；NULL=价格未定；非 CNY 括号注币种（只存不算）。"""
    if price_cents is None:
        return "价格未定"
    text = f"{price_cents / 100:.2f}".rstrip("0").rstrip(".")
    if (currency or "CNY").upper() == "CNY":
        return f"{text}元"
    return f"{text} {(currency or '').upper()}"


def render_listing(products: list[Product]) -> str:
    """列举模板：前 MAX_LISTED 件 + 总数（换行文本，零引用——前端不做商品卡）。"""
    total = len(products)
    lines = [f"本店在售商品共 {total} 件："]
    for index, product in enumerate(products[:MAX_LISTED], 1):
        lines.append(
            f"{index}. {product.name}（{product.category}）· "
            f"{format_price(product.price_cents, product.currency)}"
        )
    if total > MAX_LISTED:
        lines.append(f"（仅列出前 {MAX_LISTED} 件，共 {total} 件）")
    return "\n".join(lines)


def render_quote(product: Product) -> str:
    """单品报价模板（实时行价；调用方保证 price_cents 非空）。"""
    return (
        f"{product.name}（{product.category}）售价 "
        f"{format_price(product.price_cents, product.currency)}。"
    )


@dataclass(frozen=True)
class CatalogAnswer:
    """回落命中：模板正文 + 工具式轨迹（{name, arg, result}，SSE/消息同形）。"""

    content: str
    tool: dict[str, Any]


def try_catalog_answer(
    db: Session, question: str, hits: list[dict[str, Any]]
) -> CatalogAnswer | None:
    """retrieve 之后调用：有命中直接 None；无命中且目录意图时列举/报价。

    miss（空店/无匹配/无价）返回 None——调用方走既有拒答+转人工+落缺口
    （去补=上新/改价，回落读实时行价故同问可答）。DB 只读商品表（工具
    数据源，不是中台对象）。
    """
    if hits:
        return None
    intent = catalog_intent(question)
    if intent is None:
        return None
    products = list(db.scalars(select(Product).order_by(Product.id)))
    if intent == "listing":
        if not products:
            return None
        return CatalogAnswer(
            content=render_listing(products),
            tool={
                "name": TOOL_NAME,
                "arg": CATALOG_LISTING_ARG,
                "result": f"在售 {len(products)} 件",
            },
        )
    product = match_product(question, products)
    if product is None or product.price_cents is None:
        return None
    price_text = format_price(product.price_cents, product.currency)
    return CatalogAnswer(
        content=render_quote(product),
        tool={"name": TOOL_NAME, "arg": product.name, "result": f"{product.name} · {price_text}"},
    )
