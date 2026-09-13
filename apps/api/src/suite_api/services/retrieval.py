"""检索索引：切块（发布事务内写入）+ 中文词法打分检索（ADR 0023 工程选型）。

- 切块（CONTEXT「切块」）：证据句级。文档按行 -> 句号/分号切句，长句再按
  逗号顿号断；「字段：值」行整行成块（规格行的字段名与值是一个证据单元，
  拆开反而让「净含量」命中不到值）；对话转写（「顾客：…/客服：…」）按行/
  轮成块（一轮=一条证据）。块数上限 MAX_CHUNKS 防长文档炸索引。
- 打分：无分词器、无向量（本刀工程选型，ADR 0023）下用字符二元组（bigram）
  集合做词法匹配。score = |查询有效 bigram ∩ 块有效 bigram| / sqrt(块有效
  bigram 数)：分子衡量证据对查询的覆盖（覆盖越多越相关）；分母做长度归一——
  长块天然更容易撞上查询 bigram，不归一会长块霸榜，sqrt（而非线性）是 BM25 式
  折中，保留长证据句的些许优势。块侧与查询同一停用字口径：分母不含功能字
  bigram（长块被「的了/是有」撑大分母属于纯噪音）。单字查询退化为 unigram
  （无 bigram 可言）。
- 停用词：bigram 含任一纯功能字（的了/是/有/怎么…）即无效。宁缺勿滥
  （0018 无证据不答）：「保温杯的净含量」只留下 保温/温杯/净含/含量 四个
  有效 bigram，跨虚词噪音（杯的/的净）不参与命中。
- 实体亲和重排（第 79 刀，ADR 0048）：打分后乘 per-query 实体乘数
  1+3*title_affinity——问句对候选资产标题的 IDF 加权 bigram 覆盖。字段块
  （品牌：/净含量：…）跨资产同文时词法并列是跨商品混淆的病根（75 刀实测），
  问句点名的资产由此破开并列；问句与标题无 bigram 交集时乘数恒 1（中性面，
  标题含字段词/回流首问时纯字段问也有亲和——设计内取舍，见 ADR 0048）。
- retrieve 只查「当前已发布版本」的 chunks：join assets 的
  current_published_version_id 指针（0006）。待人洗/已接入不出现由 join
  语义保证而非事后过滤（0017：索引=已发布的派生视图，指针前移命中集合
  跟着走）。空查询/纯停用词 -> 空。
"""

import hashlib
import math
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, Product, RetrievalChunk
from suite_api.services.machine_wash import QA_FIELD, redact
from suite_api.services.synonyms import apply_synonyms
from suite_platform.storage import ObjectStorage

# ---------- 评论证据的适用域（第 66 刀，审计刀 13 P0-2） ----------
# 顾客评论（review_import）是**商品体验**证物，不是**服务状态**的证据：实测
# 「到货了吗」引用无关评论（演示库实测 asset 172 洗发水评论「到货后看着很多」，
# 词法撞上；审计刀 13 原报为英文 account_access 评论系归属误差，同型成立）、
# 「退货运费多少钱」首引衣服评论（「还要我自己承担运费」分数 0.447 压过退货
# 政策 0.408）——短评论块 bigram 少、分数天然高，**分数阈值分不开**（实测坏
# case 0.4–0.5，金标真命中最低 0.17），分得开的是证据**类别**与问句**意图**。
#
# 判据（金标集实测零回归；第 69 刀扩观点尾白名单）：问句命中服务状态词
# （到货/退货/物流…）且**无**观点标记（怎么样/值得入手吗/好吗/结实吗…）→
# 评论块不作为证据。观点问豁免是关键：
# 金标 19 条期望评论资产的 case（pos-005/029/032、conf-012..015、syn-002 等）
# **全部**带观点标记——问的是评价本身，评论正是对的证据。带订单号的问句在
# 引擎步 1 已被订单工具接走，到不了这里；本闸对 MCP search_published 同样
# 生效（证据语义全出口一致，ADR 0018 修订）。
# 词表第 66 刀评审补齐（金标程序化核验零回归）：服务词 += 售后/换货/客服/保修
# （「换货流程怎么走」曾唯一命中评论「我要求换货」0.707、「售后政策」首引投诉
# 评论——与退货运费引衣服评论同型）；观点标记 += 如何/体验/快不快/慢不慢/快吗/
# 慢吗（「发货快吗」「快递包装结实吗」「物流真的很快吗」是有服务词的**观点问**，
# 评论正是对的证据，漏标记会把它们饿成拒答——评审实测 5 命中→0）。
# 已知取舍（真值表钉住）：「不值得/不推荐」含「值得/推荐」仍豁免——豁免侧从宽
# （把评论放进来）比错杀轻：错杀把可答变拒答，从宽退回闸前的词法命中形态。
# 复审审计 P1（B 轴/C 轴同源）：+= 咋样/什么样/可靠——67 刀把「咋」字族在
# 报价侧合法化（咋样卖/咋价）后，观点表不同步会让「物流咋样」被饿成拒答
# （同义的「物流怎么样」照常引评论作答，同意图两种归宿）；可靠=靠谱的同义词。
# 复审审计 P2：服务词 += 包邮/寄件/送货/取件/退回/维修/安装/发票/改地址
# （当前演示库评论切块零条含这些词，无活缺陷；补上是防将来灌入评论语料
# 后同型 P0 复活——均为无歧义的服务状态词）。
_SERVICE_STATE_RE = re.compile(
    "到货|发货|物流|快递|收货|签收|退货|退款|运费|订单|单号|售后|换货|客服|保修"
    "|包邮|寄件|送货|取件|退回|维修|安装|发票|改地址"
)


# 第 69 刀（评审订正后的最终形态）：**默认拦 + 观点尾白名单放行**。
# 初版用「服务词 + 事实问尾白名单」收窄，评审证伪：事实问尾是**开集**——
# 「退货运费谁承担」「查物流」「发货地是哪里」等未枚举的同义事实问全部漏放，
# 引用无关评论作答（「谁承担」下衣服评论 0.894 夺冠），正是 66 刀 P0 的同义
# 复现。按「误判比漏检贵」反转：**保持 66 刀默认拦**（服务词 + 无观点标记），
# 把复审审计记的「形容词+吗 体感问错杀」改由**扩观点尾白名单**解决——
# 漏枚举形容词的代价从「错误答案」（贵）降回「诚实拒答留缺口」（便宜，
# 治理台可见可补）。观点尾 = 品质形容词+吗 的一批实测形态（好吗/专业吗/
# 严实吗/结实吗/及时吗/顺利吗/麻烦吗/暴力吗/给力吗/耐心吗/墨迹吗/爽快吗…
# 与既有 快吗/慢吗/快不快/慢不慢 同族）。
# 审计刀 14 B 轴 P0：**去掉裸「如何」**——「退货运费如何计算」「售后如何处理」是
# 事实问（如何+动词），裸收「如何」把它们豁免给评论（衣服评论 0.447 压过退货
# 政策——66 刀 P0 同义复现）。按「漏枚举=选便宜失效模式」重排：如何只以**观点
# 复合词**出现（服务如何/体验如何/态度如何…），裸「这家物流如何」落诚实拒答
# （便宜）；「如何赔付」等未枚举事实动词全部正确被拦（贵方向闭合）。
_OPINION_RE = re.compile(
    "怎么样|怎么想|好不好|好不好用|评价|靠谱|值得|推荐|好用吗|好用不|体验"
    "|快不快|慢不慢|快吗|慢吗|咋样|什么样|可靠"
    "|好吗|专业吗|严实吗|结实吗|及时吗|顺利吗|麻烦吗|暴力吗|给力吗|耐心吗|墨迹吗|爽快吗"
    "|稳吗|周到吗|贴心吗|满意吗|差吗|烂吗|牛吗|省心吗|准时吗|好评"
    "|服务如何|体验如何|态度如何|速度如何|感觉如何|咋如何"
)


def is_opinion_question(query: str) -> bool:
    """问句是否在问体验/评价（观点尾白名单命中）。供跨模块复用（第 70 刀订单
    状态问的澄清路径用它放过「物流怎么样」类观点问——评论正是它们的证据）。"""
    return bool(_OPINION_RE.search(query))


def excludes_review_evidence(query: str) -> bool:
    """该问句下评论块是否不作为证据（纯函数便于单测与金标复算）。

    服务状态词在场（订单/物流/售后语境）且无观点标记（怎么样/好吗/快吗/结实吗…
    在问体验）→ 拦。**默认拦**是取舍结果：漏枚举的观点尾会错杀体感问（诚实拒答
    留缺口，便宜），漏枚举的事实问尾会引用无关评论作答（66 刀 P0，贵）——两害
    相权取其轻（第 69 刀初版用事实尾白名单，评审证伪后反转，见上方注释）。
    """
    return bool(_SERVICE_STATE_RE.search(query)) and not _OPINION_RE.search(query)

# 切块数上限：单版本切块超限即截断（防长文档/长转写把索引写爆；截断即丢尾部证据，
# 属防炸取舍，上传上限 2MB 文本按句切通常远小于此）
MAX_CHUNKS = 200
# 句子超过此长度再按逗号/顿号断（字符数，含中英文标点后的正文）
_LONG_SENTENCE_CHARS = 40
# 过短的片段不成证据（单字/双字碎片信息量不足，弃之宁缺勿滥）
_MIN_CHUNK_CHARS = 2

# 「字段：值」行：短前缀 + 中英冒号 + 非空值（净含量：550毫升 / 储存条件：常温避光）。
# 切块 gate 用；回答组装（answer.py）用同词表字符的捕获版 _FIELD_CAPTURE_RE。
FIELD_LINE_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9 ]{1,12}\s*[:：]\s*\S.{0,200}$")

# 停用字表：纯功能字/代词/语气字。bigram 含任一即视为无效（见模块 docstring）。
_STOP_CHARS = frozenset(
    "的了呢吧啊吗么呀哦嘛啦是在有和与及或对其被把让跟请问我你您他她它这那个哪些"
    "怎么怎样如何多少还就也很都最太挺不没会把要能可以"
)

# 查询候选行上限：当前已发布版本的 chunks 全量拉回内存打分。单店规模
# （已发布资产 x200 块）在千行级；破万说明规模变了，届时应把匹配下推 DB
# 或引入向量（ADR 0023 把算法定为可替换的工程标定）。
_MAX_CANDIDATE_ROWS = 10000


class ChunkingError(Exception):
    """切块失败（对象字节不可读/非 UTF-8）：发布事务必须整体回滚——索引没写
    就不算发布成功（CONTEXT「已发布」：发布时切块入索引）。"""


def _field_line_chunks(line: str) -> list[str]:
    return [line]


def _sentence_chunks(sentence: str) -> list[str]:
    """长句再按逗号/顿号断，保留最小长度；断完仍超长不再细切（证据完整性优先）。"""
    if len(sentence) <= _LONG_SENTENCE_CHARS:
        return [sentence] if len(sentence) >= _MIN_CHUNK_CHARS else []
    parts = [p.strip() for p in re.split(r"[，,、]", sentence)]
    return [p for p in parts if len(p) >= _MIN_CHUNK_CHARS] or [sentence]


def chunk_document(text: str) -> list[str]:
    """文档切块：逐行 -> 「字段：值」行整行成块，其余按句号/分号切句。"""
    chunks: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if FIELD_LINE_RE.match(line):
            chunks.extend(_field_line_chunks(line))
            continue
        for sentence in re.split(r"[。；;]", line):
            sentence = sentence.strip().strip("，,、")
            chunks.extend(_sentence_chunks(sentence))
    return chunks[:MAX_CHUNKS]


def chunk_dialogue(text: str) -> list[str]:
    """对话转写切块：按行/轮成块（「顾客：…」「客服：…」一轮=一条证据）。"""
    return [line.strip() for line in text.splitlines() if len(line.strip()) >= _MIN_CHUNK_CHARS][
        :MAX_CHUNKS
    ]


def chunk_text(text: str, kind: str) -> list[str]:
    if kind == "dialogue":
        return chunk_dialogue(text)
    return chunk_document(text)


def read_index_text(storage: ObjectStorage, object_key: str) -> str:
    """从对象存储读回版本字节并解码（发布事务内调用；ADR 0003 字节只住对象存储）。"""
    try:
        data = storage.get_bytes(object_key)
    except FileNotFoundError as exc:
        raise ChunkingError(f"对象存储中找不到版本字节，无法切块入索引: {object_key}") from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChunkingError("版本字节不是合法 UTF-8 文本，无法切块入索引") from exc


def _confirmed_entry_value(confirmed_fields: dict[str, Any], field: str) -> Any:
    entry = confirmed_fields.get(field)
    return entry.get("value") if isinstance(entry, dict) else None


def qa_pair_chunks(confirmed_fields: dict[str, Any]) -> list[str]:
    """confirmed qa_pairs -> 每对一块「问：{q}\\n答：{a}」（第 12 刀，0017 顺延）。

    只有人洗确认（或修订继承）的 QA 对入索引；机洗草稿（extracted 未确认）与
    弃权不进——0010 confirmed 才进索引。形状不合法的值直接跳过（写入端已校验，
    这里是索引侧的防御）。
    """
    value = _confirmed_entry_value(confirmed_fields, QA_FIELD)
    if not isinstance(value, list):
        return []
    chunks: list[str] = []
    for pair in value:
        if not isinstance(pair, dict):
            continue
        q, a = pair.get("q"), pair.get("a")
        if isinstance(q, str) and isinstance(a, str) and q.strip() and a.strip():
            chunks.append(f"问：{q.strip()}\n答：{a.strip()}")
    return chunks


def _field_text(fields: dict[str, Any], name: str) -> str | None:
    """从 {字段: {value}} 形状取字符串值（非 dict/非 str/空串 -> None）。"""
    value = _confirmed_entry_value(fields, name)
    return value if isinstance(value, str) and value.strip() else None


def index_chunks_for_version(
    storage: ObjectStorage,
    object_key: str,
    kind: str,
    confirmed_fields: dict[str, Any],
    extracted_fields: dict[str, Any] | None = None,
) -> list[str]:
    """发布事务用的切块入口：confirmed 块 + 版本正文切块（seq 续排）。

    确认字段块的目的：机洗没抽到/抽散的字段，其「字段名：值」仍可被检索
    （问「净含量」命中字段块）；只用 confirmed（写回口径同 0010：confirmed
    才是操作者背书的值），弃权/未确认不进。dialogue 的确认字段是 qa_pairs
    结构化数组，每对成一块「问：…/答：…」（第 12 刀）。行对象由调用方
    （发布事务）按 (asset_id, version_no, seq) 落库。

    块序（第 16 刀 P1#5）：confirmed qa_pairs 块排在转写正文块**之前**——
    确认过的 QA 是人洗成果、价值密度最高，而块表截断在 [:MAX_CHUNKS]：
    长转写（>200 轮）下 QA 排尾会被静默截掉，排首则任何截断先丢正文。
    其余确认字段块仍续在正文块后（与第 12 刀口径一致）。

    正文文本源按 kind 分派（第 46 刀，裁决 5；评审 P1 修正）：
    - video：正文**只来自 transcript 字段**（confirmed 优先，回落 extracted），
      **永不读对象字节**——字节可能是 mp4 二进制（有源录像）或旧路径的时间码
      文本，前者读回是乱码、后者能读但那是历史形态；用「字段缺失就回落读字节」
      会留下一条静默乱码/409 的路（评审 P1-2）。字段缺失或为空 = 正文为空
      （视频资产无必填字段闸，照常可发布，只是没有正文块）。
    - 其余 kind：照旧 read_index_text(storage, object_key) 读字节切块。
    """
    from suite_api.services.publishing import confirmed_value

    if kind == "video":
        transcript = _field_text(confirmed_fields, "transcript")
        if transcript is None and extracted_fields is not None:
            transcript = _field_text(extracted_fields, "transcript")
        body = transcript or ""  # 永不读字节：mp4 二进制不进切块（评审 P1-2）
    else:
        body = read_index_text(storage, object_key)

    chunks = qa_pair_chunks(confirmed_fields)
    chunks.extend(chunk_text(body, kind))
    for field in sorted(confirmed_fields):
        if field == QA_FIELD:
            continue
        value = confirmed_value(confirmed_fields, field)
        if value is not None:
            chunks.append(f"{field}：{value}")
    return chunks[:MAX_CHUNKS]


# ---------- 词法打分（纯函数，便于单测） ----------


def _normalize(text: str) -> str:
    """去空白与标点，只留字词字符（中英文/数字）。"""
    return "".join(ch for ch in text if ch.isalnum())


def query_terms(query: str) -> frozenset[str]:
    """查询的有效词法单元集合：bigram（过滤含停用字的），单字查询退化 unigram。

    空查询/纯停用词 -> 空集合（retrieve 对空集合直接返回空，0018 宁缺勿滥）。
    单字也停用 -> 空。
    """
    normalized = _normalize(query)
    if not normalized:
        return frozenset()
    if len(normalized) == 1:
        return frozenset() if normalized in _STOP_CHARS else frozenset({normalized})
    return frozenset(
        bigram
        for bigram in (normalized[i : i + 2] for i in range(len(normalized) - 1))
        if bigram[0] not in _STOP_CHARS and bigram[1] not in _STOP_CHARS
    )


def _chunk_terms(chunk: str) -> frozenset[str]:
    """块侧词法单元：与查询同口径（bigram 过停用字；单字退化 unigram，停用单字为空）。

    分母同样滤掉功能字 bigram：长块里堆的「的了/是有」不该参与长度归一——
    否则功能字把 sqrt(块单元数) 撑大，长证据句被纯噪音压分。
    """
    return query_terms(chunk)


def score_chunk(terms: frozenset[str], chunk: str) -> float:
    """打分：|交集| / sqrt(块单元数)。见模块 docstring 的理由；无命中=0。"""
    if not terms:
        return 0.0
    chunk_units = _chunk_terms(chunk)
    if not chunk_units:
        return 0.0
    overlap = len(terms & chunk_units)
    if overlap == 0:
        return 0.0
    return overlap / math.sqrt(len(chunk_units))


# ---------- 忠实度闸（第 40 刀，ADR 0044 §二：覆盖不足不生成） ----------

# 覆盖度阈值：问句有效 bigram 与命中块并集的交集占比低于该值且命中数 ≤1 时
# 降级模板（工程初值，评测集可校准——校准只动这一个常量）。
FIDELITY_MIN_COVERAGE = 0.4


def coverage_ratio(query: str, chunks: list[str]) -> float:
    """忠实度闸的证据覆盖度（纯函数便于单测）：|query_terms ∩ ∪chunk_terms|
    / |query_terms|。与打分函数同一套词法单元（query_terms/_chunk_terms）。

    query_terms 为空（空查询/纯停用词）恒 1.0——该形态根本到不了生成（retrieve
    返回空 -> 0018 拒答），闸保守不触发。"""
    query_units = query_terms(query)
    if not query_units:
        return 1.0
    if not chunks:
        return 0.0
    chunk_units: frozenset[str] = set().union(*(_chunk_terms(chunk) for chunk in chunks))
    return len(query_units & chunk_units) / len(query_units)


# ---------- 过期降权（第 39 刀保鲜；打分公式的后处理乘数，不动 score_chunk） ----------

# 过期资产的块打分乘以该乘数（score*=0.5）后再排序——打分口径本体（ADR 0023
# 工程标定）保持不变，可独立校准。
STALE_MULTIPLIER = 0.5
# 过期阈值（天）：env STALE_DAYS 可配，缺省 90。读取在调用时（非 import 时），
# 便于测试 monkeypatch 与部署侧不重启调参。
STALE_DAYS_DEFAULT = 90


def stale_days() -> int:
    raw = os.environ.get("STALE_DAYS", "")
    return int(raw) if raw else STALE_DAYS_DEFAULT


def is_stale(last_verified_at: datetime | None, *, now: datetime, days: int) -> bool:
    """过期判定（纯函数便于单测）。last_verified_at 为 NULL 时恒 False。

    保守裁决（spec 内嵌裁决，钉死在此）：曾考虑「NULL=未灌即按 stale 处理」
    逼操作者发布后点一次验证，但存量/演示库资产 last_verified_at 全 NULL，
    NULL 降权=整库降权，评测基线（run_eval 96 条大集）数字会被打破——故改
    「NULL 不降权」：只有**显式验证过**（发布快照或重新验证）后距今超过
    days 天的资产才降权。仪表先可观测，降权动作保守。
    """
    if last_verified_at is None:
        return False
    return now - last_verified_at > timedelta(days=days)


# ---------- 实体亲和重排（第 79 刀，ADR 0048；跨商品混淆病根的正解） ----------

# 亲和乘数强度（工程标定，实验矩阵定稿 a3：overall@1 66.2->86.2，见
# scripts/eval/rerank_experiment.py 与 rag-eval-report 第 79 刀节）。
# per-query 实体信号与 75 刀被拒的全局来源权重的本质区别：乘数只在「问句
# 点名了某个资产」（标题区分性 bigram 被覆盖）时 >1，问句与标题无交集恒 1。
AFFINITY_ALPHA = 3.0


def title_idf(titles: list[str]) -> dict[str, float]:
    """按候选资产标题全集算每个标题 bigram 的 idf（log(1 + N/(1+df))）。

    「规格（OFF）/评论/说明」这类跨资产共享 bigram 天然低 idf，商品名/
   专名 bigram 天然高 idf——亲和信号由区分性词主导，共享词几乎不贡献。
    空标题不参与统计（其亲和恒 0=中性，不因无标题被显式惩罚）。
    """
    df: dict[str, int] = {}
    n = 0
    for title in titles:
        if not title:
            continue
        n += 1
        for term in query_terms(title):
            df[term] = df.get(term, 0) + 1
    return {term: math.log(1 + n / (1 + count)) for term, count in df.items()}


def title_affinity(terms: frozenset[str], title: str | None, idf: dict[str, float]) -> float:
    """IDF 加权的问句-标题覆盖（纯函数便于单测与实验复算）。

    = Σ_{t∈标题bigram∩问句bigram} idf(t) / Σ_{t∈标题bigram} idf(t)：
    标题的区分性 bigram 被问句覆盖的比例。标题为空/无有效 bigram 时返回
    0——乘数 1，不因无标题被显式惩罚。注意「问句与标题无 bigram 交集才
    恒 0」：标题含字段词（如回流资产的标题=首问「保温杯的净含量是多少？」）
    时纯字段问也得到亲和，这是设计内行为（ADR 0048 已知取舍）。
    """
    if not title:
        return 0.0
    title_terms = query_terms(title)
    if not title_terms:
        return 0.0
    total = sum(idf.get(term, 0.0) for term in title_terms)
    if total <= 0.0:
        return 0.0
    covered = sum(idf.get(term, 0.0) for term in title_terms & terms)
    return covered / total


# ---------- 实体存在性闸（OOV 收口，第 82 刀） ----------

# 判据常量（实验矩阵定稿，scripts/eval/oov_criterion.py；见 rag-eval-report §82）：
# 实体段里「连续零出现串」的最短字数——专名形态（雀巢咖啡/星巴克杯子/戴森吸尘器）。
# 取 4 是刻意的保守值：3 字会把「你们卖什么」（3 字）与同义改写（syn-005 的
# 「退换政策说明」零出现串 3 字）卷进来（刀 12 口径：误判比漏检贵）。代价是
# 「华为手机」（3 字）漏判走既有拒答路径——可接受。
OOV_MIN_SPAN = 4

# 语料规模护栏（条）：零出现信号在**小语料**上不可靠——库内文本太少时「零出现」
# 几乎是必然（评审回退实验实测：护栏置 0 时测试库形态误杀 4 例既有用例）。
# 低于此条数不判 OOV、退回既有路径（39 刀「NULL 不降权」同族的保守裁决：数据
# 条件不足时不启用）。演示库符合条件的候选行 ~470 条、生产单店千级；小店资料
# 不足时本闸静默不生效。测试替身（MagicMock db）的 .all() len 恒 0，同一守卫
# 挡下（替身不测本闸——本闸行为由 test_oov_gate 的真库种子覆盖）。
OOV_MIN_CORPUS_ROWS = 100


def _field_name_terms(chunks: list[str]) -> frozenset[str]:
    """库内字段名词表（数据驱动，免手工维护）：从「字段：值」行块的字段名抽 bigram。

    净含量/条码/品牌/配料… 这些词在库内遍地出现，不该算「实体段」的一部分——
    问「乐事薯片的净含量是多少」的实体是「乐事薯片」，不是「净含量」。
    """
    terms: set[str] = set()
    for chunk in chunks:
        match = FIELD_LINE_RE.match(chunk)
        if match:
            terms |= query_terms(chunk.split("：")[0].split(":")[0])
    return frozenset(terms)


def oov_product_match(db: Session, entity: str) -> str | None:
    """OOV 实体串是否对应**本店在售**（商品名双向包含 / 类目名或别名命中）。

    第 86 刀（Owner 裁决）：顾客问到未上架商品是**必然且重要**的（他不知道店里
    有什么，主动点名商品=最高价值的意图信号）。OOV 收口要区分两种本质不同的事：
    - 商品在库但资料没上架 -> 「资料还在补充中」+ **落知识缺口**（确实是知识待补）
    - 商品不在库 -> 「本店暂时没有这款」+ **只建工单不落缺口**（不是知识问题，
      补文档也补不出来；缺口池语义=知识待补，不该被经营范围问题污染）
    两种都建工单（42 刀语义：顾客要人）。

    判据（预热测量定稿）：实体串与商品名**双向包含**（「保温杯」⊂「钛钢保温杯」；
    库外实体 0.00–0.20），或命中类目名/别名（「笔记本电脑」是类目，商品名是具体
    型号——不补这条会把它误报成「本店无此商品」）。返回命中的商品名/类目名。
    """
    from suite_api.services.catalog_tools import CATEGORY_ALIASES  # 延迟导入避循环

    normalized = _normalize(entity)
    if not normalized:
        return None
    products = db.execute(select(Product.name, Product.category)).all()
    categories = {name for _n, name in products if name}
    categories |= set(CATEGORY_ALIASES) | set(CATEGORY_ALIASES.values())
    # 两个方向语义不同（预热测量后收紧）：
    # - 实体串 ⊂ 库内名（顾客用简称：「保温杯」⊂「钛钢保温杯」）——任意长度命中；
    # - 库内名 ⊂ 实体串（实体里含一个库内名片段）——**要求库内名 ≥3 字**，否则
    #   短泛词会把库外实体误命中（实测「星巴克杯子」被商品名片段「杯子」命中）。
    def matched(candidate: str) -> bool:
        token = _normalize(candidate)
        if not token:
            return False
        # 方向 A：顾客用简称（实体串 ⊂ 库内名，如「保温杯」⊂「钛钢保温杯」）——任意长度
        if normalized in token:
            return True
        # 方向 B：实体串里含库内名片段——要求库内名 ≥3 字（短泛词如「杯子」不算：
        # 「星巴克杯子」曾被商品名「杯子」误命中）
        return len(token) >= 3 and token in normalized

    for alias in sorted(categories, key=len, reverse=True):
        if matched(alias):
            return alias
    for name, _category in products:
        if matched(name or ""):
            return name
    return None


# 语料快照缓存（第 85 刀性能）：模块级单槽（键校验，读到旧值也不会错——摘要不符即重建）。
# 多 worker 各自缓存；键 = **库身份 + 已发布资产的 (id, 指针, 标题) 序列**摘要：发布/修订/
# 治理改标题/增删都会变更；**废弃**靠该资产从行集出列使摘要改变。
_corpus_cache: (
    tuple[str, tuple[frozenset[str], frozenset[str], dict[int, str], dict[str, float]]] | None
) = None


def _corpus_snapshot(
    db: Session,
) -> tuple[frozenset[str], frozenset[str], dict[int, str], dict[str, float]]:
    """取语料快照 (corpus bigram 集, 字段名词表, titles, 标题 idf)。

    第 85 刀测量：oov_verdict 每次 ask 要 483 行文本 + 全量 bigram 抽取 + idf 重算，
    11.8ms 比 retrieve 本身还慢。快照只取决于已发布资产集合及其内容 → 按摘要缓存，
    命中时只剩一笔摘要查询（几十行）。
    """
    assets = db.execute(
        select(Asset.id, Asset.current_published_version_id, Asset.title)
        .where(
            Asset.status == "published",
            Asset.discarded_at.is_(None),
            Asset.current_published_version_id.isnot(None),
        )
        .order_by(Asset.id)
    ).all()
    # 键含**库身份**（评审 P1）：同进程多库（测试每 module DROP/CREATE、将来多租户）
    # 可能 (id,指针,标题) 完全相同而 chunk 不同——不带库名会串库。
    key = hashlib.sha1(
        (str(db.get_bind().url) + repr([tuple(row) for row in assets])).encode()
    ).hexdigest()
    global _corpus_cache
    if _corpus_cache is not None and _corpus_cache[0] == key:
        return _corpus_cache[1]
    chunks = list(
        db.scalars(
            select(RetrievalChunk.chunk)
            .join(
                AssetVersion,
                (AssetVersion.asset_id == RetrievalChunk.asset_id)
                & (AssetVersion.version_no == RetrievalChunk.version_no),
            )
            .join(
                Asset,
                (Asset.id == AssetVersion.asset_id)
                & (Asset.current_published_version_id == AssetVersion.id)
                & (Asset.status == "published")
                & (Asset.discarded_at.is_(None)),
            )
            .order_by(RetrievalChunk.id)
            .limit(_MAX_CANDIDATE_ROWS)
        )
    )
    corpus: set[str] = set()
    titles: dict[int, str] = {}
    for asset_id, _pointer, title in assets:
        corpus |= query_terms(title or "")
        titles[asset_id] = title or ""
    for chunk in chunks:
        corpus |= query_terms(chunk)
    payload = (frozenset(corpus), _field_name_terms(chunks), titles, title_idf(list(titles.values())))
    _corpus_cache = (key, payload)
    return payload


def oov_verdict(db: Session, question: str) -> str | None:
    """问句疑似点名了**库内不存在**的实体时返回该实体串（供拒答文案），否则 None。

    症状（审计刀 16 C 轴实测）：问「雀巢咖啡的配料是什么」（库内无此商品）会引
    花生酱/雪糕的配料作答——用户看到别人家商品的资料；同形态的「乐事薯片」却
    拒答，归宿随机（取决于模型措辞）。

    判据（实验矩阵定稿，golden 96 条零误杀——命中 10 条全为 refusal 组）：
    1. 问句剔除观点标记（`_OPINION_RE`）与库内字段名词后取有效 bigram（**实体段**）；
    2. 实体段存在 ≥ ``OOV_MIN_SPAN`` 字的**连续零出现串**（该串的全部 bigram 不在
       库内任何已发布文本里）——专名形态；同义改写的零出现串是分散的（「退换政策
       说明」对库内「退货政策说明」），串长够不到；纯字段问的实体段为空；
    3. 且实体段对**全部已发布资产标题**的最高亲和（第 79 刀信号）为 0——没有任何
       资产被点名（真匹配如「保温杯」（aff=1.0）、中间地带如「Erdbeeren 巧克力」
       （aff=0.75）都被这一条挡下）。
    三条同时成立才判 OOV：宁可漏判走既有路径（拒答/作答），绝不误杀可答问句。

    调用方：引擎的 RAG 分支（目录/工具回落之后、LLM 生成之前），用**本问**而非
    拼接 query（多轮拼接会让上一问的实体参与判定）。返回串当前**只供判真**
    （拒答文案仍是既有固定模板 + 问句摘要——「点名实体」的文案留作记债）。
    MCP/缺口验证不调（裁决，见 closeout）：它们的语义是「返回证据/验证命中」，
    不是「作答」——本闸是作答决策，不是检索排序（与 79 刀 rerank 的四出口同语义
    不同类；把 OOV 下推到 retrieve 会让 MCP 搜索对「库里没有的实体」也空手而归，
    那是另一条产品语义，未裁）。
    """
    stripped = _OPINION_RE.sub(" ", question)
    if not _normalize(stripped):
        return None
    if not query_terms(stripped):
        # 早退（评审 P2）：纯停用字/无有效 bigram 的问句在全库查询之前返回。
        # 注：字段问（「净含量是多少」）仍需查询——「库内字段名词表」要扫 chunk
        # 才能抽（entity_terms 在其后判断）；本闸每 ask 多一次与 retrieve 同量级
        # 的全库扫描，合并/缓存优化记债（评审 P2-4，千级语料可辩护）。
        return None
    # 语料规模护栏（见 OOV_MIN_CORPUS_ROWS 注释）：小语料不判——小库的「零出现」
    # 不构成实体不存在的证据。第 85 刀改为轻量 count（只需行数，不必拉文本）。
    corpus_size = db.execute(
        select(func.count())
        .select_from(RetrievalChunk)
        .join(
            AssetVersion,
            (AssetVersion.asset_id == RetrievalChunk.asset_id)
            & (AssetVersion.version_no == RetrievalChunk.version_no),
        )
        .join(
            Asset,
            (Asset.id == AssetVersion.asset_id)
            & (Asset.current_published_version_id == AssetVersion.id)
            & (Asset.status == "published")
            # 第 83 刀：废弃资产（0042 discarded_at）不进检索语料。
            & (Asset.discarded_at.is_(None)),
        )
    ).scalar_one()
    # 读不到可信计数（替身/异常返回非 int）即**不启用判据**——fail-closed 与
    # 护栏同方向（语料不足不判），不是「为替身留分支」：真库恒 int。
    if not isinstance(corpus_size, int) or corpus_size < OOV_MIN_CORPUS_ROWS:
        return None
    # 语料快照（第 85 刀性能）：corpus/字段名词表/titles/idf 只取决于「当前已发布
    # 资产集合及其内容」，按资产摘要（id+指针+标题+废弃位）缓存——命中时只剩一笔
    # 轻量摘要查询（几十行），省掉每 ask 的数百行文本传输 + 全量 bigram 抽取 + idf
    # 重算。失效由摘要保证：发布/修订改指针、治理改标题、废弃、增删资产都变摘要值。
    corpus, field_terms, titles, idf = _corpus_snapshot(db)
    if not titles:
        return None

    entity_terms = query_terms(stripped) - field_terms
    if not entity_terms:
        return None
    miss = entity_terms - corpus
    if not miss:
        return None
    # 最长连续零出现串（在归一化串上逐位扫；记起点以便回切实体串）
    norm = _normalize(stripped)
    longest = current = 0
    best_start = 0
    for i in range(len(norm) - 1):
        if norm[i : i + 2] in miss:
            current += 1
            if current > longest:
                longest = current
                best_start = i - current + 1
        else:
            current = 0
    span = longest + 1 if longest else 0
    if span < OOV_MIN_SPAN:
        return None
    # 用快照里的 idf（此前这里重算一次并 shadow —— 评审 P2 的死缓存）
    if any(title_affinity(entity_terms, t, idf) > 0.0 for t in titles.values()):
        return None
    # 返回实体串（归一化形态，去掉了标点/虚词）供拒答文案点名
    return norm[best_start : best_start + span]


# ---------- 检索（只查当前已发布版本，join 保证） ----------


def retrieve(
    db: Session, query: str, *, top_k: int = 5, gate_question: str | None = None
) -> list[dict[str, Any]]:
    """检索当前已发布版本的切块，按分数降序返回 [{asset_id, version_no, chunk, score}]。

    查询词取「原查询 ∪ 同义词归一后」的**并集**（第 36 刀接线后 after 复跑改并集：
    替换式把原词 bigram 弄丢，正例组 -5pp、混淆组 -13.3pp——净负，按 goal §6.2.2
    「无提升不留」纪律改 token expansion：ES synonym 工业惯例同款是扩展不是替换）。
    并集在数学上只增查询词不删（score 分子=|交|只增不减），无表词查询零漂移；
    表内词问句追加同组 bigram 撞回原文证据。多轮记忆的代词拼接检索词
    （conversation_memory.retrieval_query）走同一入口，同受益。块侧不动。

    候选集 SQL：retrieval_chunks join asset_versions join assets，其中
    asset_versions.id == assets.current_published_version_id 且
    (chunk.asset_id, chunk.version_no) == (asset_versions.asset_id, version_no)。
    待人洗/已接入资产的指针为 NULL，join 天然不出现（0004/0017：由 join 语义
    保证而非事后过滤）；发布新版指针前移后，旧版 chunk 因 version_no 不再
    匹配指针版本而自动出榜（派生视图语义）。

    过期降权（第 39 刀保鲜）：候选 SQL 随带 assets.last_verified_at，打分后
    对「显式验证过且距今 > stale_days() 天」的资产块 score*=STALE_MULTIPLIER
    （后处理乘数，不动 score_chunk 本体；NULL 不降权=保守裁决，见 is_stale）。

    评论适用域（第 66 刀，ADR 0018 修订）：候选 SQL 随带 assets.source_kind，
    ``gate_question``（缺省即 query）命中服务状态词且无观点标记时 review_import
    块不进候选——「什么算证据」在此单点定义，引擎/缺口验证/MCP/评测共用。

    实体亲和重排（第 79 刀，ADR 0048）：打分后对每个候选资产乘
    ``1 + AFFINITY_ALPHA * title_affinity``（问句 terms 对该资产标题的 IDF
    加权覆盖）。跨商品同文字段块的并列由此被「问句点名的资产」破开；
    问句与标题无 bigram 交集时乘数恒 1（中性面——标题含字段词/回流首问
    时纯字段问也有亲和，ADR 0048 已知取舍）。返回的 score 是重排后的名次分。
    """
    # 查询侧同义词扩展（0023 词法口径内的确定性扩展，非向量）：原查询词与
    # 归一后词取并集——只增不删，保证既有命中不丢（after 评测裁决的修正）。
    terms = query_terms(query) | query_terms(apply_synonyms(query))
    if not terms:
        return []
    now = datetime.now(UTC)
    days = stale_days()
    # 第 66 刀：source_kind 随候选集一并取回（join 本来就在，零额外查询）——
    # 评论适用域闸（excludes_review_evidence）在打分前过滤。
    # 评论适用域闸按 **gate_question（本问）** 判定，缺省即 query：多轮拼接检索
    # （retrieval_query 拼上一问）会让上一问的观点标记豁免本问的服务态问句——
    # 「物流怎么样→它到货了吗」拼出「怎么样」，garbage 评论原样放行（评审 P1）。
    # 引擎拼接时显式传本问；缺口验证/MCP/评测的 query 就是本问，不传即同义。
    drop_reviews = excludes_review_evidence(gate_question or query)
    rows = db.execute(
        select(
            RetrievalChunk.asset_id,
            RetrievalChunk.version_no,
            RetrievalChunk.chunk,
            Asset.last_verified_at,
            Asset.source_kind,
            Asset.title,
        )
        .join(
            AssetVersion,
            (AssetVersion.asset_id == RetrievalChunk.asset_id)
            & (AssetVersion.version_no == RetrievalChunk.version_no),
        )
        .join(
            Asset,
            (Asset.id == AssetVersion.asset_id)
            & (Asset.current_published_version_id == AssetVersion.id)
            & (Asset.status == "published")
            # 第 83 刀：废弃（0042 discarded_at）不进检索——此前只过滤 status，
            # 「已发布后废弃」的资产仍在候选里（本刀实测引用里出现废弃资产）。
            & (Asset.discarded_at.is_(None)),
        )
        .order_by(RetrievalChunk.id)
        .limit(_MAX_CANDIDATE_ROWS)
    ).all()
    # 0038 修订（第 21 刀，审计刀 4 P0 簇出口 1）：字节不动、出口必掩——
    # 收口点裁决取「retrieve 返回处统一 redact」：单点侵入最小，且同时覆盖
    # 两个消费者（llm.build_prompts 证据行进厂商 prompt、answer.compose_answer
    # 降级模板行进顾客可见回答），顺带覆盖 MCP search_published 的 chunk
    # （同属跨进程边界出口，ADR 0038 修订段「MCP 响应」口径）。打分/去重/排序
    # 全程仍用原文块（score_chunk 吃 comprehension 的 chunk 变量，不动检索
    # 打分）；索引行与版本字节永不回写掩码（不可变锁死，出口只现掩）。
    # 第 79 刀（ADR 0048）：实体亲和重排——候选资产的标题 bigram 被**本问**
    # terms 覆盖的比例（IDF 加权）作为 per-query 乘数 score*(1+3*affinity)。
    # 病根：字段块（品牌：/净含量：…）跨资产同文时词法并列，短块微弱胜出、
    # (asset_id, chunk) 稳定出榜——问「钛钢保温杯的净含量」召回 OFF 他品的
    # 同文块（75 刀实测）。问句点名的资产亲和>0 被抬上来；问句与标题无
    # bigram 交集时乘数恒 1（中性面；标题含字段词/回流首问时纯字段问也有
    # 亲和——ADR 0048 已知取舍，混淆组的提升部分正来自此）。与打分/stale/
    # 评论闸同在 retrieve 内=引擎双通道/MCP/缺口验证/评测四出口同语义。
    titles = {asset_id: title for asset_id, _, _, _, _, title in rows}
    idf = title_idf(list(titles.values()))
    aff_of = {
        asset_id: title_affinity(terms, title, idf) for asset_id, title in titles.items()
    }
    scored = [
        {
            "asset_id": asset_id,
            "version_no": version_no,
            "chunk": redact(chunk),
            # 过期降权是乘数后处理：打分本体（score_chunk）不动，只有显式验证
            # 过且超过阈值才乘 0.5（null 恒不降——保守裁决见 is_stale docstring）
            # 实体亲和是第二个乘数（>=1，问句与标题无交集恒 1）——与 stale
            # 正交相乘：过期被点名资产 0.5*(1+3*aff) 最高 2.0 仍可压过新鲜
            # 未点名资产（stale 是软降权、亲和是强信号，ADR 0048 取舍）。
            "score": score
            * (STALE_MULTIPLIER if is_stale(verified_at, now=now, days=days) else 1.0)
            * (1.0 + AFFINITY_ALPHA * aff_of[asset_id]),
        }
        for asset_id, version_no, chunk, verified_at, source_kind, _title in rows
        if (score := score_chunk(terms, chunk)) > 0.0
        # 评论适用域（第 66 刀）：服务状态问（无观点标记）下评论块不算证据
        and not (drop_reviews and source_kind == "review_import")
    ]
    # 去重：文档原文的「字段：值」行与确认字段生成的块可能同文（合法：两路证据），
    # 但回答组装不该复读同一句——按键去重保序
    unique: dict[tuple[int, int, str], dict[str, Any]] = {}
    for hit in scored:
        key = (hit["asset_id"], hit["version_no"], hit["chunk"])
        if key not in unique:
            unique[key] = hit
    # 分数降序；并列按 (asset_id, chunk) 稳定排序，结果可重现
    results = sorted(
        unique.values(), key=lambda hit: (-hit["score"], hit["asset_id"], hit["chunk"])
    )
    return results[:top_k]
