"""库存工具（ADR 0037，第 16 刀修订）：只读 get_stock + 模板组装回答。

- 分派双前置（第 16 刀；**词表现值见 ``STOCK_KEYWORD_PATTERN``**——第 56 刀加
  有没有/有…吗、第 65 刀加 卖完/卖光/售罄/断货，本段不复制词表以免漂移）：
  词表命中才进库存路径（订单号优先，第 13 刀在先）；**类目/别名聚合（第 56/62
  刀）与单品匹配都带纯度闸**（``stock_residual``/``stock_product_residual``，
  第 62/65 刀），词表命中但两路都不中 -> 回既有检索路径（归宿对齐词条：无证据
  拒答留缺口，0018/0024），不再转人工；词表外问题走既有检索路径零漂移。
- ``match_product`` 纯代码商品匹配：商品名与问题最长公共子串（按字符）≥2 字
  命中，多命中取最长 LCS、平手取 id 小——不做 NLP/LLM/分词库（类目**口语别名**
  另有 ``catalog_tools.CATEGORY_ALIASES``，第 56/62 刀，两路共用 ``category_targets``）。
- ``get_stock`` 只读 products.stock 列（工具数据源，不是中台对象，0002 不升
  格）：返回 ``{found, product_name, stock}``，``stock=None`` 即未设置；DB 异常
  捕获为 ``{error: True}`` 不向上炸——转人工由引擎 handoff 分支表达，失败不拿
  检索顶（0018，吞异常+尽力回滚同 order 模式，异常进服务端日志 P1#6）。
- 回答用确定性模板（v1 不调 LLM）：stock>0「有货」含件数；stock==0「暂时无货」
  是事实数据不是失败（正常 answer）；NULL/异常 → kind="handoff"。
  工具条一行摘要 ``summarize_stock_result`` 与引用芯片语义分离（UX-NOTES §6）。
"""

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from suite_api.models import Product

# 吞异常转 handoff 的对外行为不变（0018：失败不拿检索顶），但线上必须能看到
# 原因——审计刀 3 P1#6：捕获处 logger.exception（原文只进服务端日志）
logger = logging.getLogger(__name__)

# 词表分派（0037；第 16 刀修订 P1#3 收窄）：只留「到货状态」四类直陈词。
# 去掉「库存/现货」——通用词吞掉已发布的政策文档（「库存政策是什么」命中即
# 跳过检索，政策永远查不到）；去掉「剩」——「保温杯还剩多少毫升」这类规格问句
# 含商品名会被 LCS 命中误答「有货」。规格类词（净含量/保质期/材质）本就不在列。
# 收窄后仍要求商品匹配前置（chat_engine 分派处）：裸「有货吗」无商品名回检索。
# 第 56 刀补「有…吗 / 有没有…」口语形态（「你们有笔记本吗」）——只做**路由器**：
# 命中后查库存，查到（含类目聚合）就走事实模板，查不到原样落回检索/拒答路径，
# 所以放宽词表不会误答（「有优惠吗」查不到 -> 照旧走 RAG）。
# 第 65 刀补「卖完/卖光」：问的是库存是否售罄。**浏览器验收抓的层间缝**——
# 虚词表（`_STOCK_FILLER_WORDS`）先补了「卖完/卖光」，但路由词表没补，「书都
# 卖完了吗」（无「有」字，`有.{0,12}吗` 不命中）根本进不了库存工具，照旧拒答
# ——直调 `query_stock` 的单测测的是工具层，路由层要单测钉。
STOCK_KEYWORD_PATTERN = re.compile(
    "有货|没货|无货|缺货|有没有|有.{0,12}吗|卖完|卖光|售罄|断货"
)

# 商品名与问题最长公共子串的最小命中长度（中文字符计）
_MATCH_MIN_LCS = 2

# 问货虚词（第 62 刀）：类目聚合的**纯度闸**。此前 `_category_stock` 只判
# `token in question`，于是别名/类目名**当修饰语**的问句被整体吸进类目聚合——
# 实测 4 例：「手机壳有货吗」→「智能手机共 10 件」、「电视柜有货吗」→电视机、
# 「平板支撑有货吗」→平板电脑、「笔记本电脑包有货吗」→笔记本电脑（配件/家具/
# 箱包被当成类目本身）。与报价的 `price_residual` 同一精神：扣掉命中字面后必须
# **只剩问货虚词**，还剩实质词（壳/柜/支撑/包）就不聚合，照旧回落检索。
#
# **不复用 `price_residual`**：两份虚词表各有各的语境（问价词 vs 问货词），
# 并成一张会互相放水（价格侧会去剔「有货」，库存侧会去剔「多少钱」）。
#
# 词表**原子词 + 长词在前**（`sorted(key=len, reverse=True)`，同 `_SPEC_WORD_RE`）：
# 手写顺序踩过一次坑——「没有货吗」被「有」先吃掉会剩「没...货」残渣，「没有货」
# 必须排在「有」前面。原子词是硬要求：「有货」拆成「有」「货」照样吃残渣。
#
# **收「店里」不收裸「店」**（评审 P2 实测）：裸「店」会让「手机店/书店/电脑店
# 有货吗」残渣为空（店是**卖这个的店**，不是这个类目本身）。收两字的「店里/店铺/
# 门店」既挡得住店铺形态，又放过「你们店里有笔记本吗」这种自然的门店问法。
# 残留的同类边界：「库存书有货吗」仍会聚合到图书（「库存」是必需虚词，见
# 「有库存吗」）——罕见的定语形态，记为已知边界而非缺陷。
# 第 65 刀补「卖完/卖光」（原子词）：「书都卖完了吗」是问库存的自然形态，
# 此前残渣剩「完/光」被闸挡回；评审后同批补裸「没」（「卖光了没」——收「没有」
# 没收「没」）、裸「全」（「全卖完了吗」——收「都」没收「全」）与量词原子词
# 一台/一本/…（「书一本都卖完了吗」，与报价侧第 62 刀的量词对称；**必须逐个
# 枚举**——本表 join 时每个词都过 `re.escape`，写成字符类会被转义成字面量）。
# 仍**不收**「到货」——「到货了吗/到货了没」问的是**到货时间**，库存工具
# 答不了时间，照旧回落检索/拒答留缺口。审计刀 13 B 轴同批补：售罄/断货（问的
# 就是库存，65 刀只补了卖完/卖光）、这/那/本（「经济学原理这本书有货吗」——
# 指示词与指示量词（这个/那台/这本——**含裸 个/台**，否则「这个保温杯还有货吗」
# 残字「个」被挡回，审计刀 13 顺带实测的快路径假拒面）；其中「书」是类目别名
# 不能进虚词表，收复合词「本书」（「经济学原理这本书」拆成 这+本书，类目分支
# 仍被「经济学原理」残字挡住不误吸）、多（「库存多吗」）。
# **不收**「少」：「还剩多少」是数量问句，库存合计答不了「多少」的精确语义，
# 照旧回落（与「到货」同判）。
_STOCK_FILLER_WORDS = (
    "有没有货",
    "没有货",
    "有没有",
    "有货",
    "没货",
    "无货",
    "缺货",
    "现货",
    "存货",
    "备货",
    "库存",
    "还有",
    "还剩",
    "卖完",
    "卖光",
    "没有",
    "没",
    "全",
    "售罄",
    "断货",
    "本书",
    "这",
    "那",
    "本",
    "个",
    "台",
    "多",
    "一台",
    "一本",
    "一部",
    "一个",
    "一包",
    "一盒",
    "一件",
    "一套",
    "一杯",
    "一双",
    "你们",
    "咱们",
    "本店",
    "小店",
    "店铺",
    "店里",
    "门店",
    "请问",
    "当前",
    "现在",
    "目前",
    "商品",
    "产品",
    "这种",
    "这类",
    "剩",
    "有",
    "货",
    "一下",
    "都",
    "还",
    "在",
    "上",
    "卖",
    "进",
    "的",
    "了",
    "吗",
    "么",
    "呢",
    "啊",
    "吧",
    "、",
    "，",
    "？",
    "?",
    "！",
    "!",
    "。",
    " ",
    "　",
)
_STOCK_FILLER_RE = re.compile("|".join(re.escape(word) for word in sorted(_STOCK_FILLER_WORDS, key=len, reverse=True)))


def stock_residual(question: str, token: str) -> str:
    """扣掉命中字面（别名/类目名）与问货虚词后剩余的「实质成分」（纯函数）。

    空 = 问句主体就是「类目（或别名）+ 问货」，可以按类目聚合；非空 = 命中的字面
    只是**修饰语**（手机壳/电视柜/笔记本电脑包），照旧回落检索。
    """
    return _STOCK_FILLER_RE.sub("", question.replace(token, "")).strip()


def stock_product_residual(question: str, product_name: str) -> str:
    """单品问货的纯度残渣（纯函数）：剔 **LCS 命中片段** + 问货虚词。

    与 `stock_residual` 的区别在剔除方式，同报价侧 `price_residual` 的取舍：
    商品名允许**部分名**（「保温杯有货吗」对「钛钢保温杯」——`match_product`
    按 LCS≥2 命中，闸用同一口径剔除，否则部分名问货会被静默挡回检索）；类目/
    别名 token 则保证字面出现，直接 replace。残渣非空 = 问句主体不是
    「商品名 + 问货」（服务/规格/政策问，如「卖完了还能刻字吗」「想退货怎么办」），
    照旧回落检索——第 65 刀评审实测，缺这道闸时刻字服务问被硬答成「有货 42 件」。
    """
    rest = question
    if product_name:
        fragment = lcs_fragment(question, product_name)
        if len(fragment) >= _MATCH_MIN_LCS:
            rest = question.replace(fragment, "")
    return _STOCK_FILLER_RE.sub("", rest).strip()

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


def lcs_fragment(a: str, b: str) -> str:
    """两串的最长公共子串（按字符，DP + 起点回填；短文本够用）。

    与 `_lcs_len` 同源口径：`match_product` 用它判命中，报价纯度闸用它**剔除命中
    片段**（部分名问价「保温杯多少钱」对商品「钛钢保温杯」时，被剔除的是「保温杯」
    而不是要求全名子串——否则闸会把合理问价挡回 RAG，评审 P1）。
    """
    if not a or not b:
        return ""
    best_len = 0
    best_end = 0
    prev = [0] * (len(b) + 1)
    for i, ca in enumerate(a, 1):
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                value = prev[j - 1] + 1
                cur[j] = value
                if value > best_len:
                    best_len = value
                    best_end = i
        prev = cur
    return a[best_end - best_len : best_end]


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
        # 尽力恢复会话可用（属性访问可能触发惰性加载，连接失效等）
        logger.exception("库存工具读取商品库存失败: product_id=%s", getattr(product, "id", None))
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("库存工具回滚失败（吞异常后会话可能不可用）")
        return {"error": True}
    return {"found": True, "product_name": name, "stock": stock}


def query_stock(db: Session, question: str) -> dict[str, Any]:
    """库存工具入口：列商品 -> match_product -> get_stock（引擎分派后单点调用，
    单测在此打桩）。商品未命中 {found: False}；DB 异常（列查询或读取失败）
    {error: True}；命中 {found: True, product_name, stock}。

    **类目聚合（第 56 刀）**：商品名不中时再看**类目**（含口语别名，如「笔记本」
    →「笔记本电脑」）——「你们有笔记本吗」问的是这一类有没有货，逐件匹配商品名
    必然落空（审计刀 11 C-P1-3）。命中返回 `{found: True, category, total,
    in_stock, stock_sum}`（`product_name` 给类目名，供文案与工具条复用）。

    **类目先于单品（第 62 刀，对齐报价侧）**：商品名里含完整类目名时（「WANDS
    家具（演示）」含「家具」），单品路径会先命中把「你们有家具吗」答成**这一个
    商品**的库存——报价侧同一缺陷已在审计刀 11 P1 修过（先类目后单品）。类目
    分支自带纯度闸，问句里带具体型号/修饰语（残渣非空）时不会命中，此时才走单品。

    **单品也有纯度闸（第 65 刀评审实修）**：此前闸只装在类目分支，「保温杯卖完
    了还能刻字吗」被 LCS 命中商品名后**硬答库存**（刻字服务问被答成「有货 42
    件」）。修法与报价侧 `price_residual` 同构：剔 `lcs_fragment` 命中片段
    （部分名「保温杯」↔「钛钢保温杯」的容错口径）+ 问货虚词，残渣非空即视为
    实质问句，回落检索。
    """
    try:
        products = list(db.scalars(select(Product).order_by(Product.id)))
    except SQLAlchemyError:
        logger.exception("库存工具列取商品失败: question=%s", question)
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("库存工具回滚失败（吞异常后会话可能不可用）")
        return {"error": True}
    category_hit = _category_stock(question, products)
    if category_hit is not None:
        return category_hit
    product = match_product(question, products)
    if product is None:
        return {"found": False}
    if stock_product_residual(question, product.name) != "":
        return {"found": False}
    return get_stock(db, product)


def _category_stock(question: str, products: list[Product]) -> dict[str, Any] | None:
    """类目（或口语别名）聚合库存：命中返回聚合结果，否则 None。

    只做**计数与合计**（不编造每件明细）：total 类目商品数 / in_stock 有货件数
    （stock>0）/ stock_sum 已设置库存合计（None=都没设置）。候选表与报价侧同源
    （`catalog_tools.category_targets`，函数内导入避开模块环），但**纯度闸各装各的**
    （`stock_residual`）：第 62 刀实测，缺闸时「手机壳有货吗」被答成「智能手机共 10 件」。
    """
    from suite_api.services.catalog_tools import category_targets

    categories = {p.category for p in products}
    for token, category in category_targets(categories):
        if token not in question or stock_residual(question, token) != "":
            continue
        members = [p for p in products if p.category == category]
        stocks = [p.stock for p in members if p.stock is not None]
        return {
            "found": True,
            "category": category,
            "product_name": category,
            "total": len(members),
            "in_stock": sum(1 for stock in stocks if stock > 0),
            "stock_sum": sum(stocks) if stocks else None,
        }
    return None


def summarize_stock_result(result: dict[str, Any]) -> str:
    """工具条一行结果摘要（灰底 mono：get_stock(钛钢保温杯) → 此处）。"""
    if result.get("error"):
        return "查询失败"
    if not result.get("found"):
        return "未找到商品"
    if "category" in result:
        return f"{result['category']} {result['total']} 件 · 有货 {result['in_stock']}"
    stock = result["stock"]
    if stock is None:
        return "未设置"
    if stock > 0:
        return f"有货 · {stock} 件"
    return "暂时无货"


def render_stock_answer(result: dict[str, Any]) -> str:
    """事实分支模板（0037：stock==0 是事实数据不是失败，正常回答）。

    类目聚合（第 56 刀）单独一行：件数 + 有货件数 + 合计库存（未逐件设置就只说件数）。
    """
    if "category" in result:
        total = result["total"]
        in_stock = result["in_stock"]
        stock_sum = result.get("stock_sum")
        tail = f"，库存合计 {stock_sum} 件" if stock_sum is not None else "（库存未逐件设置）"
        return f"{result['category']}共 {total} 件，其中有货 {in_stock} 件{tail}。"
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
