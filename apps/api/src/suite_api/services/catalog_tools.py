"""目录回落（第 41 刀，ADR 0045）：retrieve 无命中时的商品列举/报价。

- 触发（第 50 刀起两条入口）：``try_catalog_answer`` 在 **retrieve 返回 []**
  时回落（列举与报价都走它）；``try_price_answer`` 补「**有命中也要报价**」那
  条路——价格是商品行的事实，演示库带商品名的问句常有命中，只看空命中会把补齐
  的行价永远埋在 RAG 里。两条路径**共用同一套判定**（命中商品名 + 有价 + 纯度），
  不会一条严一条松（审计刀 10 P0 的收口）。
- 证据语义：工具式模板（不调 LLM、citations 恒空、kind=answer）——商品行
  是工具数据源（与 stock/orders 同口径：商品不是中台对象，0002），不是
  引用（不碰 0007 引用锚）。
- miss 归宿：报价路径无匹配/无价、或空店，返回 None——调用方走既有拒答+转人工+
  落缺口（去补=上新商品/改价：回落读实时行价，同问可答）。列举路径恒有答案
  （只列已定价；一件都没定价也如实说「目前都没有公布价格」并请顾客直接问商品名）。
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
from suite_api.services.handoff_tickets import HUMAN_REQUEST_RE
from suite_api.services.stock_tools import _MATCH_MIN_LCS, lcs_fragment, match_product

# 工具式模板的轨迹名（SSE tool 事件与消息 tool 列同形状 {name, arg, result}，
# 与 get_order_status/get_stock 同为「已发生的只读动作留档」，不是证据引用）。
TOOL_NAME = "catalog"
# 列举形态的轨迹参数（无商品指向，恒为目录本身）。
CATALOG_LISTING_ARG = "目录"
# 列举上限：只列**已定价**商品的前 N 件（未定价不再逐条铺——20 行「价格未定」
# 会把在售规模与可用条目一起淹掉，而顾客真正能接着问的是「这件多少钱」）。
MAX_LISTED = 8

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
# 显式要真人/投诉/举报不抢。第 42 刀起 run_ask 已把同词表快路径前置在所有
# 工具之前，故本闸在引擎路径内**永不触发**（超集关系）；保留它是为
# catalog_intent 被单独调用时仍保持「显式要人不回落目录」的纯函数契约。
_HUMAN_RE = HUMAN_REQUEST_RE

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
    """列举模板：总数 + 已定价商品前 MAX_LISTED 件（换行文本，零引用——前端不做商品卡）。

    只列已定价：未定价的行对顾客没有可行动信息（既不知价也不好接着问），逐条铺会
    把在售规模与可用条目淹掉（第 41 刀实测：115 件里前 20 件有 18 行「价格未定」）。
    未定价商品让顾客直接问商品名——问价走报价回落，读的是实时行价。

    前置：调用方保证 ``products`` 非空（空店由 try_catalog_answer 前置返回 None 走
    拒答+转人工+落缺口）；这里仍给空店一句人话，不让纯函数吐荒谬文案。
    收尾行只在真有未定价商品时出现——全店已定价时说「其余未定价」是事实错误。
    """
    total = len(products)
    if total == 0:
        return "本店还没有上架商品。"
    priced = [product for product in products if product.price_cents is not None]
    if not priced:
        return (
            f"本店在售商品共 {total} 件，目前都没有公布价格。"
            "直接问商品名，我帮你查规格与库存。"
        )
    if len(priced) == total:
        lines = [f"本店在售商品共 {total} 件，均已定价："]
    else:
        lines = [f"本店在售商品共 {total} 件，其中已定价 {len(priced)} 件："]
    for index, product in enumerate(priced[:MAX_LISTED], 1):
        lines.append(
            f"{index}. {product.name}（{product.category}）· "
            f"{format_price(product.price_cents, product.currency)}"
        )
    truncated = len(priced) > MAX_LISTED
    if truncated:
        lines.append(f"（仅列出前 {MAX_LISTED} 件已定价商品）")
    if len(priced) < total:
        lines.append("其余未定价，直接问商品名。")
    elif truncated:
        lines.append("其余商品直接问名字。")
    return "\n".join(lines)


def render_quote(product: Product) -> str:
    """单品报价模板（实时行价；调用方保证 price_cents 非空）。"""
    return (
        f"{product.name}（{product.category}）售价 "
        f"{format_price(product.price_cents, product.currency)}。"
    )


def render_category_quote(category: str, priced: list[Product], total: int) -> str:
    """类目报价模板（第 52 刀）：按类目问价给「件数 + 价格区间（或统一价）」。

    为什么要有它：列举（「你们卖什么」）刚说「共 115 件，均已定价」，紧接着问
    「笔记本电脑多少钱？」却拒答——`match_product` 只认**商品名**，类目名不是商品名
    （审计刀 10 P1，9 个类目全中招）。类目价是**聚合事实**，由商品行现算、不编造：
    区间取该类目已定价行的最小/最大值；都同价就说「均为」。
    """
    prices = [p.price_cents for p in priced if p.price_cents is not None]
    low, high = min(prices), max(prices)
    currency = priced[0].currency
    if low == high:
        shown = f"均为 {format_price(low, currency)}"
    else:
        shown = f"价格 {format_price(low, currency)}–{format_price(high, currency)}"
    unpriced = total - len(prices)
    tail = f"（另有 {unpriced} 件未定价）" if unpriced > 0 else ""
    return (
        f"{category}共 {total} 件，其中已定价 {len(prices)} 件，{shown}{tail}；"
        f"问具体型号给你准确价。"
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
    return _quote_answer(question, products)


# 报价问句的虚词/语境词（提纯时剔除）：与列举的 `_TRIVIAL_RE` 同精神，另加问价
# 用词本身。**扣掉商品名、价格词、这些虚词后必须什么都不剩**，报价才成立。
_PRICE_RE = re.compile(
    "多少钱|价格|售价|定价|报价|收费|贵不贵|便宜|花多少|多少|"
    # 「什么」是纯疑问词（「帆布包什么价格？」是标准问价形态，评测集里就有这例；
    # 漏了它会把合法问价静默挡回 RAG）
    "什么|咋|请问|问一下|你们|咱们|的|是|要|买|这个|这款|这件|那|款|"
    r"[的吗呢啊嘛呀吧呗哦噢]|、|，|？|\?|！|!|。| |　"
)


def price_residual(question: str, product_name: str) -> str:
    """扣掉**命中的商品名片段**与问价虚词后剩余的「实质成分」（纯函数）。

    报价回落只在**问句主体就是「商品名 + 问价」**时生效：像「保温杯刻字怎么收费」
    「钛钢保温杯怎么保养，收费吗」这类问句虽然含价格词（`收费`）且能匹配到商品名，
    问的却是**服务/规格**（该走文档），报价模板答「售价 129元」是答非所问；
    剩余非空即视为实质问句 -> 不回落。

    剔除对象是**命中的公共子串**（`lcs_fragment`），不是要求全名子串：商品
    「钛钢保温杯」被问成「保温杯多少钱」是口语常态，`match_product` 按 LCS≥2 命中，
    纯度闸用同一口径剔除，否则部分名问价会被静默挡回 RAG（审计刀 10 P1）。
    """
    rest = question
    if product_name:
        fragment = lcs_fragment(question, product_name)
        if len(fragment) >= _MATCH_MIN_LCS:
            rest = question.replace(fragment, "")
    return _PRICE_RE.sub("", rest).strip()


def try_price_answer(db: Session, question: str) -> CatalogAnswer | None:
    """报价判定（第 50 刀，**修订 ADR 0045 的「有命中永不回落」**）。

    为什么需要它：原口径要求 `retrieve == []` 才回落到目录，可演示库里已发布
    资产不少，**带商品名的问句常常有检索命中**——于是「X 多少钱」走 RAG，而证据
    里没有行价，模型只能答「证据未覆盖价格」；把 115 件商品的价补齐了顾客仍问不
    出价（第 50 刀验收实测）。价格是**商品行的事实**（0002 商品不是中台对象），
    行就是权威来源，文档里的价是快照、可能过期。

    口径：报价意图（`catalog_intent == "price"`）+ 命中商品名 + 有价 + **纯度**
    （三道判定都在共用的 `_quote_answer` 里——审计刀 10 P0：闸只装在这里时，
    空命中那条旧路径照旧抢答服务问句）。

    与老路径的分工：`try_catalog_answer` 只在**空命中**时报价；本函数在**有命中**
    时也报价（第 50 刀修订：价格是商品行的事实，演示库带商品名的问句常有命中，
    老口径下补齐的价永远问不出来）。两条路径共用同一套判定，故不会一条严一条松。

    **列举不受影响**：仍走 `try_catalog_answer` 的空命中闸（否则「你们卖什么」会
    抢掉有目录类证据的回答）。
    """
    if catalog_intent(question) != "price":
        return None
    products = list(db.scalars(select(Product).order_by(Product.id)))
    return _quote_answer(question, products)


def _quote_answer(question: str, products: list[Product]) -> CatalogAnswer | None:
    """报价模板（两条路径共用：空命中回落 / 报价判定）。

    三道闸都在这里（**共用**，审计刀 10 P0：此前闸只装在 `try_price_answer`，
    空命中那条路径照旧抢答服务问句——「家具送货安装怎么收费？」在无命中库上被答
    成「售价 899元」）：
    1. 命中商品名（LCS ≥2）或命中**类目名**（第 52 刀：列举说「均已定价」却答不出
       类目价，等于自相矛盾）；2. 该商品有价 / 该类目有已定价商品；3. **纯度**——
       扣掉命中片段与问价虚词后必须什么都不剩（问句主体就是「名字 + 问价」）。
    """
    # **先类目、后单品**（审计刀 11 P1）：商品名里含完整类目名时（「WANDS 家具
    # （演示）」含「家具」），单品路径会先命中并把「家具多少钱？」答成**这一个商品**
    # 的价——类目聚合（件数/区间/未定价计数）被静默吞掉，一旦该类目再来一件不同价
    # 的商品就答错。类目路径自带纯度闸：问句扣掉类目名后还有实质内容（如具体型号
    # 「笔记本 A 多少钱」）就不会命中，此时才走单品。
    category_answer = _category_quote(question, products)
    if category_answer is not None:
        return category_answer
    product = match_product(question, products)
    if product is not None and product.price_cents is not None:
        if price_residual(question, product.name) == "":
            price_text = format_price(product.price_cents, product.currency)
            return CatalogAnswer(
                content=render_quote(product),
                tool={
                    "name": TOOL_NAME,
                    "arg": product.name,
                    "result": f"{product.name} · {price_text}",
                },
            )
    return None


def _category_quote(question: str, products: list[Product]) -> CatalogAnswer | None:
    """按类目问价（第 52 刀）：类目名在问句里，且问句主体就是「类目 + 问价」。

    纯度闸同款（`price_residual`：扣掉类目名与问价虚词后必须为空），否则
    「笔记本电脑刻字怎么收费」这类服务问会被答成类目价。类目无已定价商品 -> None
    （沿既有拒答 + 缺口口径：去补 = 改价/上新）。
    """
    categories = sorted({p.category for p in products}, key=len, reverse=True)
    for category in categories:
        if category not in question or price_residual(question, category) != "":
            continue
        members = [p for p in products if p.category == category]
        priced = [p for p in members if p.price_cents is not None]
        if not priced:
            return None
        # 混币种不做类目聚合（跨币种比大小无意义，v1 单币种是约定不是保证）——
        # 宁可拒答留缺口，也不给一个「3元–5美元」的假区间（审计刀 11 P2）
        currencies = {p.currency for p in priced}
        if len(currencies) > 1:
            return None
        content = render_category_quote(category, priced, len(members))
        return CatalogAnswer(
            content=content,
            tool={
                "name": TOOL_NAME,
                "arg": category,
                "result": f"{category} {len(members)} 件 · {_range_text(priced)}",
            },
        )


def _range_text(priced: list[Product]) -> str:
    """类目轨迹的价格段（与正文同口径：复用 format_price，不手搓「//100 元」）。"""
    prices = [p.price_cents for p in priced if p.price_cents is not None]
    currency = priced[0].currency
    low, high = min(prices), max(prices)
    if low == high:
        return format_price(low, currency)
    return f"{format_price(low, currency)}–{format_price(high, currency)}"
    return None
