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
- 切块嵌入写端（第 105 刀，向量基础设施 A1）：``embed_version_chunks`` 在发布
  事务**提交后**补写 ``retrieval_chunks.embedding``（迁移 0034 的 pgvector 列，
  只备料不动检索——retrieve 打分路径一行不改，融合是 106 刀）。未配置/失败 =
  块照写、embedding NULL+日志，发布不被云调用阻塞。
- 稠密稀疏融合（第 106 刀，ADR 0058）：词法管线（打分/stale/亲和）产出的
  命中集上叠加**加权线性**的向量分——词法分 per-query min-max 归一 +
  VECTOR_WEIGHT × cosine（矩阵 ``scripts/eval/fusion_matrix.py`` 三形态
  661 配置的定案形态 C-w=0.5-tau0.60-lexgate-before）。两条纪律闸（两轮
  矩阵实测教训，缺一拒答率崩）：**cos ≥ VECTOR_COS_FLOOR**（bge-m3 在本
  语料上 refusal 近邻与正例相似度重叠，无闸时无关块灌进证据）+
  **词法空手闸**（词法零命中时向量不无中生有——0018 宁缺勿滥在融合层的
  延伸，拒答语义完全由词法路决定）。NULL 块 fail-open 到词法（向量路只
  加分不排除）；未配置 EMBED_API_KEY / 嵌入或查询失败 = 纯词法零变化。
  查询向量按 (库, 模型, 问句) 进程内 LRU 缓存——同问句不重复调云 API。
"""

import hashlib
import json
import logging
import math
import os
import re
from collections import OrderedDict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, Product, RetrievalChunk
from suite_api.services.machine_wash import QA_FIELD, redact
from suite_api.services.synonyms import apply_synonyms
from suite_api.settings import get_settings
from suite_platform.storage import ObjectStorage

logger = logging.getLogger(__name__)

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

    正文文本源按 kind 分派（第 46 刀，裁决 5；第 94a 刀推广到图片）：
    - **字段供体种类**（video -> transcript / image -> 图片描述）：正文**只来自
      该字段**，**永不读对象字节**——video 字节可能是 mp4 二进制（有源录像）或
      旧路径的时间码文本；image 字节是 png/jpeg/webp，解码必失败。字段缺失或为
      空 = 正文为空（两类资产都无必填字段闸，照常可发布，只是没有正文块）。
      两类的**取值面不同**：video 的 transcript 允许回落 extracted（46 刀裁决
      5——转写是资产的文本面，机洗预置即正文）；image 的「图片描述」**只认
      confirmed**（第 94a 刀：描述是 VLM **生成**内容，「只出草稿、人确认生效」
      ——未确认的草稿不进索引，顾客面前不出现未经人确认的模型文本）。
    - 其余 kind：照旧 read_index_text(storage, object_key) 读字节切块。

    **正文供体字段不进「字段：值」块循环**（第 94a 刀）：正文块本身就是该字段
    的文本，再补一块「字段名：值」等于同一句话在索引里存两份——图片的描述是
    人洗确认的常态路径，双份证据会在 prompt 的 2 个证据位上互相挤占（video 的
    同形重复只出现在确认字段继承的罕见路径，一并收口）。
    """
    from suite_api.services.machine_wash import IMAGE_DESCRIPTION_FIELD
    from suite_api.services.publishing import confirmed_value

    # 正文供体字段表（kind -> 字段名）与「可否回落 extracted」：见 docstring
    body_field = {"video": "transcript", "image": IMAGE_DESCRIPTION_FIELD}.get(kind)
    if body_field is not None:
        body = _field_text(confirmed_fields, body_field)
        if body is None and kind == "video" and extracted_fields is not None:
            body = _field_text(extracted_fields, body_field)
        body = body or ""  # 永不读字节：二进制不进切块（评审 P1-2）
    else:
        body = read_index_text(storage, object_key)

    chunks = qa_pair_chunks(confirmed_fields)
    chunks.extend(chunk_text(body, kind))
    for field in sorted(confirmed_fields):
        if field == QA_FIELD or field == body_field:
            continue
        value = confirmed_value(confirmed_fields, field)
        if value is not None:
            chunks.append(f"{field}：{value}")
    return chunks[:MAX_CHUNKS]


# ---------- 切块嵌入写端（第 105 刀，向量基础设施 A1：只备料不动检索） ----------


def set_chunk_embeddings(db: Session, rows: Sequence[tuple[int, Sequence[float]]]) -> int:
    """把 ``(块 id, 向量)`` 批量写进 retrieval_chunks.embedding（裸 SQL——pgvector
    类型非 sa 内建，ORM 不映射该列，0025 迁移同族的 sa.text 口径）。

    向量序列化为 pgvector 字面量（json.dumps 对 float 往返安全）。不 commit：
    调用方（发布补写/回填脚本）决定事务边界。返回实际更新行数。
    """
    if not rows:
        return 0
    result = db.execute(
        sa_text(
            "UPDATE retrieval_chunks SET embedding = CAST(:vector AS vector) WHERE id = :chunk_id"
        ),
        [{"chunk_id": chunk_id, "vector": json.dumps(list(vector))} for chunk_id, vector in rows],
    )
    return int(result.rowcount or 0)


def embed_version_chunks(db: Session, asset_id: int, version_no: int, chunks: Sequence[str]) -> int:
    """发布**事务提交后**的 embedding 补写（发布切块入口的第二步）。

    事务纪律（裁决）：块行在发布事务内落库（CONTEXT「已发布」：切块入索引是
    发布本体），但 embedding 是**云调用产物**——外部 IO 不进发布事务（占连接
    等外网 + 任何失败都会回滚发布，而 embedding 是增值项不是发布本体）。故
    本函数只在 commit 之后被调用：按 (asset_id, version_no, seq) 对位查回块行
    id，批量 embed（services/embedding，≤64/批），UPDATE 回填，独立小事务收口。

    三态（发布一律不被 embedding 阻塞）：
    - 未配置（无 EMBED_API_KEY）：直接返回 0，零云调用（fail-closed 不建客户端）；
    - 失败（超时/维度不符/DB 写失败）：logger.warning 后返回 0——块照写、
      embedding NULL，``scripts/realdata/backfill_embeddings.py`` 可重跑兜底；
    - 成功：返回写入条数。
    """
    from suite_api.services import embedding

    if not chunks:
        return 0
    if not embedding.is_configured():
        return 0
    try:
        vectors = embedding.embed_texts(list(chunks))
        chunk_rows = db.execute(
            select(RetrievalChunk.id, RetrievalChunk.seq)
            .where(
                RetrievalChunk.asset_id == asset_id,
                RetrievalChunk.version_no == version_no,
            )
            .order_by(RetrievalChunk.seq)
        ).all()
        updated = set_chunk_embeddings(
            db,
            [(chunk_id, vectors[seq]) for chunk_id, seq in chunk_rows if seq < len(vectors)],
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 发布不被 embedding 失败阻塞
        db.rollback()
        logger.warning(
            "资产 %s v%s 的切块 embedding 补写失败（块已入库，embedding 留 NULL，"
            "回填脚本可重跑兜底）: %s",
            asset_id,
            version_no,
            type(exc).__name__,
        )
        return 0
    return updated


# ---------- 词法打分（纯函数，便于单测） ----------


def _normalize(text: str) -> str:
    """去空白与标点，只留字词字符（中英文/数字）。"""
    return "".join(ch for ch in text if ch.isalnum())


# ---------- 第 123 刀：拉丁/数字段的整词 token（生产数据实测的权重修根） ----------
#
# 病根（OFF/Wikidata 生产重灌实测）：统一滑窗 bigram 把拉丁词切成 O(len) 个
# bigram——「Nutella」=6 个（Nu/ut/te/el/ll/la），中文属性词「净含量」=2 个
# （净含/含量）。问「Nutella的净含量是多少」时，任何含品牌字样的标题/品牌块
# 的词法分（≈1.6）稳定压过真正含答案的数值块「净含量：400g」（≈0.8）：品牌名
# 携带 3 倍于属性值的信号，证据窗口（top-k + prompt 前 2 条）被标题块占满，模型
# 只看到「某商品的规格标题」如实自述未覆盖 → 第 58 刀收口成拒答。中文合成数据
# 时代（保温杯=2 bigram，与属性词对称）不暴露；拉丁品牌名的生产数据必炸。
#
# 修法：ASCII 连续段按「字母串/数字串」整词成一个 token（小写归一——顺带修了
# 旧 bigram 的大小写敏感洞：nutella≠Nu），CJK 段照旧滑窗 bigram。「Nutella 的
# 净含量」的问句 terms 从 6+2 个 bigram 变为 {nutella}+{净含,含量} 两个信号源，
# 数值块（净含+含量 双命中）反超品牌块（nutella 单命中）。字母↔数字边界切分
# （so1002→{so,1002}、400g→{400,g}）保住「订单 1002」「400 克」这类混合问的
# 命中面。双侧（query_terms/_chunk_terms）同口径，旧索引无需重建（打分实时算）。
# OOV 判据里的「最长连续零出现串」从 bigram 位扫改为 token 字符位扫（见
# oov_verdict 内注释），CJK 行为逐位等价。


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    """原始串的 (token, 起始位, 字符长) 列表，保序保位。

    在**原始串**（非 _normalize 后）上扫描：ASCII 字母/数字段整词成 token
    （小写；字母↔数字边界切分，so1002→{so,1002}）——空格/标点天然断词，
    「Nutella, Ferrero」不会被拼成单个巨型 token（先 normalize 会丢掉分隔
    信息，Ferrero 单独问就 miss）。非 ASCII 段先剥掉非字词字符（空白/标点）
    再滑窗 bigram（过停用字；跨标点的相邻字仍成 bigram，与旧版「先 normalize
    再滑窗」逐 token 等价），孤立非停用单字退化 unigram。位置供 OOV 的零出
    现串按字符位扫（token 覆盖的字符位即其缺失位）。
    """
    out: list[tuple[str, int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isascii() and ch.isalnum():
            digit = ch.isdigit()
            j = i + 1
            while j < n:
                cj = text[j]
                if not (cj.isascii() and cj.isalnum()) or cj.isdigit() != digit:
                    break
                j += 1
            out.append((text[i:j].lower(), i, j - i))
            i = j
        else:
            chars: list[tuple[str, int]] = []
            while i < n and not (text[i].isascii() and text[i].isalnum()):
                if text[i].isalnum():
                    chars.append((text[i], i))
                i += 1
            if len(chars) == 1:
                if chars[0][0] not in _STOP_CHARS:
                    out.append((chars[0][0], chars[0][1], 1))
            else:
                for k in range(len(chars) - 1):
                    bigram = chars[k][0] + chars[k + 1][0]
                    if bigram[0] not in _STOP_CHARS and bigram[1] not in _STOP_CHARS:
                        out.append(
                            (bigram, chars[k][1], chars[k + 1][1] - chars[k][1] + 1)
                        )
    return out


def query_terms(query: str) -> frozenset[str]:
    """查询的有效词法单元集合：CJK bigram（过滤含停用字的）+ ASCII 整词 token
    （第 123 刀，字母串/数字串小写归一），单字查询退化 unigram。

    空查询/纯停用词 -> 空集合（retrieve 对空集合直接返回空，0018 宁缺勿滥）。
    单字也停用 -> 空。
    """
    return frozenset(token for token, _start, _len in _token_spans(query))


def _chunk_terms(chunk: str) -> frozenset[str]:
    """块侧词法单元：与查询同口径（bigram 过停用字；单字退化 unigram，停用单字为空）。

    分母同样滤掉功能字 bigram：长块里堆的「的了/是有」不该参与长度归一——
    否则功能字把 sqrt(块单元数) 撑大，长证据句被纯噪音压分。
    """
    return query_terms(chunk)


# ---------- 第 123 刀：字段行定向（属性问句的证据窗收口） ----------
# 属性问句（「X 的配料有什么」「X 是哪年上市的」）的答案在被问字段的行里，
# 但「字段：长值」块词法分天然低（长值串撑大 sqrt(块单元) 分母）：证据窗
# （prompt/引用各前 2）被同资产的名字头行/图片描述、兄弟商品的头行占满，
# 模型看不到数值行，如实自述未覆盖→第 58 刀收口成拒答（生产重灌实测
# 「Coca-Cola 的配料」「Sennheiser HD 800 哪年上市」均此形态）。
#
# 为什么是**重排**不是打分乘数：乘数（实测 ×10）会把同品牌兄弟商品的短
# 字段块（品牌：LU）抬到第 1——跨资产次序被打乱，评测 -4.4pp（三条同形态
# 全坏）。重排方案零分值改动：top-1 资产自己的被问字段块提到最前，跨资产
# 次序与所有分数不动——资产级 recall/MRR 零漂移，只有「赢家资产先给哪块」
# 变化（数值行进 prompt/引用，头行退居第二证据行）。
# 字段名形态上限（CJK 字数）：净含量/上市年份/保质期…最长 4 字，留余量到 6；
# 名字头行「Sennheiser HD 800 规格」无冒号/含拉丁，天然不触发
_FIELD_NAME_MAX = 6


def _field_line_named(chunk: str, terms: frozenset[str]) -> bool:
    """块是否是「字段：值」行且字段名被问句点名（纯函数便于单测）。

    形态闸（防误伤）：取首个冒号（CJK/ASCII）前的头段，须纯 CJK 且 ≤6 字——
    名字头行（无冒号或含拉丁）、QA 块（问/答 单字头且「问」进不了问句词法）
    都不触发；命中判据 = 头段词法单元 ∩ 问句单元 ≠ ∅（「上市年份：2009」对
    「哪年上市」共享 bigram「上市」即命中——字段名整词不必全在问句里）。
    """
    head = re.split(r"[：:]", chunk, maxsplit=1)[0]
    if not head or len(head) > _FIELD_NAME_MAX:
        return False
    if not all("\u4e00" <= ch <= "\u9fff" for ch in head):
        return False
    return bool(query_terms(head) & terms)


def _field_directed(fused: list[dict[str, Any]], terms: frozenset[str]) -> list[dict[str, Any]]:
    """问句点名字段时，top-1 资产自己的该字段块提到最前（保序重排，不动分）。

    只动「赢家资产先出哪块」：被问字段行进证据窗首槽（prompt/引用各取前 2），
    其余次序原样。top-1 资产无被问字段行时零变化（属性词不在场或赢家是图片/
    对话资产的常态路径）。
    """
    if not fused:
        return fused
    top_asset = fused[0]["asset_id"]
    field_hits = [
        hit for hit in fused if hit["asset_id"] == top_asset and _field_line_named(hit["chunk"], terms)
    ]
    if not field_hits:
        return fused
    field_keys = {id(hit) for hit in field_hits}
    return [*field_hits, *(hit for hit in fused if id(hit) not in field_keys)]


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

# 判据常量（实验矩阵定稿，scripts/eval/oov_criterion.py；三轮迭代的测量细节
# 见第 82 刀 closeout 与 ADR 0049）：
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
    universe = {category for _n, category in products if category}
    # 只收「真有商品」的类目（评审 P2：零商品的目标类目不该报在库——与
    # catalog_tools.category_targets 的 `target in universe` 同口径、单一真源）
    categories = set(universe)
    categories |= {alias for alias, target in CATEGORY_ALIASES.items() if target in universe}

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
        # 方向 B：实体串里含库内名片段——要求库内名 ≥3 字**且覆盖率 ≥0.6**
        # （评审 P1：裸子串会让「小米电视机顶盒」被「电视机」误命中 = 把库外
        # 实体说成在库，正中本刀要堵的洞；62 刀在报价/库存侧的纯度闸同源）。
        # 「星巴克杯子」被「杯子」（2 字）误命中由长度闸挡下。
        return len(token) >= 3 and token in normalized and len(token) / len(normalized) >= 0.6

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
    payload = (
        frozenset(corpus),
        _field_name_terms(chunks),
        titles,
        title_idf(list(titles.values())),
    )
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
        # 才能抽（entity_terms 在其后判断）。第 85 刀快照后早先「与 retrieve
        # 同量级全库扫描」的记债描述不再成立（现为 corpus count + 资产摘要两笔
        # 轻查询，OOV 命中再 +1 笔商品表）；retrieve 候选与 OOV 快照仍是两笔
        # 独立查询——彻底合并需改 retrieve 返回契约，收益已低（85 刀裁决维持）。
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
    # 最长连续零出现串（在原始问句上按 token 覆盖的字符位扫；记起点以便回切实
    # 体串）。第 123 刀：miss 里的单元已是整词/bigram token——旧「逐位取 bigram
    # 查 miss」对拉丁词天然失效（7 字词永不等于 2 字 bigram）。改扫 _token_spans
    # 的覆盖位：缺失 token 的全部字符位计缺失，最长连续缺失字符段即 span。CJK
    # 段逐位等价（雀巢咖啡 4 字 → 4）；拉丁词整段计长（Nutella → 7）；标点/空白
    # 不属于任何 token，天然切断实体串（「Nutella, Ferrero」是两个实体不是 14
    # 字一个）。实体串回切自原始串，保留原始大小写（oov_product_match 对商品名）。
    norm = stripped  # 位扫在原始串上（token 位置即原始位置；见上注）
    missing_positions: set[int] = set()
    for token, start, length in _token_spans(norm):
        if token in miss:
            missing_positions.update(range(start, start + length))
    longest = current = 0
    best_start = 0
    for i in range(len(norm)):
        if i in missing_positions:
            current += 1
            if current > longest:
                longest = current
                best_start = i - current + 1
        else:
            current = 0
    span = longest
    if span < OOV_MIN_SPAN:
        return None
    # 用快照里的 idf（此前这里重算一次并 shadow —— 评审 P2 的死缓存）
    if any(title_affinity(entity_terms, t, idf) > 0.0 for t in titles.values()):
        return None
    # 返回实体串（原始形态，保留大小写；两端虚词不在任何 token 内自然不带回）
    # 供拒答文案点名
    return stripped[best_start : best_start + span]


# ---------- 稠密稀疏融合（第 106 刀，ADR 0058；矩阵定案 C-w=0.5-tau0.60-lexgate） ----------

# 向量分权重（线性融合 final = 词法归一分 + VECTOR_WEIGHT × cosine）。
# 矩阵 661 配置定案值：w=0.5 在「保正例 98.8@1」的配置里 overall@1 最高
# （89.8，+1.0pp），w≥1 开始压动正例（词法归一域 [0,1] 被向量项盖过）。
VECTOR_WEIGHT = 0.5
# cosine 相似度下限（TAU 闸）：cos < 此值的向量命中不进融合。矩阵实测教训：
# 无闸时拒答率崩 0（向量近邻永远存在）；全局阈值封顶只救回 56.7%——0.60 是
# 「拒答保住线」与「改善保留线」的权衡点（conf@1 72.0 在 0.50-0.65 平台，
# ovr@3/MRR 在 0.60 达峰后回落）。词法路不受此闸影响（fail-open）。
VECTOR_COS_FLOOR = 0.60
# 向量路召回宽度（矩阵口径 top-10；融合出口仍由 top_k 截断）
VECTOR_TOP_N = 10

# 查询向量进程内 LRU（第 106 刀）：键 = (库 URL, embed 模型, 问句)——同问句
# 同库不重复调云 API（嵌入纯函数于问句文本，但按任务口径带库身份防多库串）。
# 维度自检：换 EMBED_MODEL 后旧缓存维度不符即作废重算（不落错维向量进融合）。
_QUERY_VEC_CACHE_MAX = 512
_query_vec_cache: OrderedDict[tuple[str, str, str], list[float]] = OrderedDict()


def query_vector_for(db: Session, query: str) -> list[float] | None:
    """问句 -> bge-m3 查询向量（LRU 缓存优先）。未配置/失败 -> None（fail-open
    纯词法），异常只留服务端日志，检索不被云调用阻塞。"""
    from suite_api.services import embedding

    if not embedding.is_configured():
        return None
    try:
        bind_url = str(db.get_bind().url)
    except Exception:  # noqa: BLE001 - 替身 db 无 bind：缓存键退通用值不阻塞
        bind_url = "<fake>"
    key = (bind_url, get_settings().embed_model, query)
    cached = _query_vec_cache.get(key)
    if cached is not None and len(cached) == embedding.EMBEDDING_DIM:
        _query_vec_cache.move_to_end(key)
        return cached
    try:
        vector = embedding.embed_texts([query])[0]
    except embedding.EmbeddingError as exc:
        logger.warning(
            "问句查询向量嵌入失败（融合退纯词法）: %s %s", query[:40], type(exc).__name__
        )
        return None
    _query_vec_cache[key] = vector
    while len(_query_vec_cache) > _QUERY_VEC_CACHE_MAX:
        _query_vec_cache.popitem(last=False)
    return vector


# 向量近邻查询：当前指针版 join 同口径 + embedding IS NOT NULL（NULL 块不进
# 向量路——词法资格由词法路保留，fail-open）+ TAU 闸（cos 相似度 ≥ 下限，
# 无关近邻不进融合）。ef_search 用 pgvector 默认 40：矩阵实测三档
# （40/80/200）在当前语料规模（598 块）零行为差异，语料破万再调。
_VECTOR_TOP_SQL = sa_text(
    """
    SELECT c.asset_id, c.version_no, c.chunk, a.source_kind,
           1 - (c.embedding <=> CAST(:qv AS vector)) AS cos_sim
    FROM retrieval_chunks c
    JOIN asset_versions v ON v.asset_id = c.asset_id AND v.version_no = c.version_no
    JOIN assets a ON a.id = v.asset_id
      AND a.current_published_version_id = v.id
      AND a.status = 'published'
      AND a.discarded_at IS NULL
    WHERE c.embedding IS NOT NULL
      AND 1 - (c.embedding <=> CAST(:qv AS vector)) >= :floor
    ORDER BY c.embedding <=> CAST(:qv AS vector)
    LIMIT :n
    """
)


def dense_candidates(db: Session, query: str, *, drop_reviews: bool) -> list[dict[str, Any]] | None:
    """向量路：查询向量 -> cosine ≥ 下限的 top-N（评论闸同口径过滤）。

    返回 None = 向量路整体不可用（未配置 key/嵌入失败/查询失败）——调用方
    fail-open 纯词法；返回行 {asset_id, version_no, chunk(redact 后), cos}。
    """
    vector = query_vector_for(db, query)
    if vector is None:
        return None
    try:
        rows = db.execute(
            _VECTOR_TOP_SQL,
            {"qv": json.dumps(vector), "floor": VECTOR_COS_FLOOR, "n": VECTOR_TOP_N},
        ).all()
    except Exception as exc:  # noqa: BLE001 - 向量查询失败不阻塞检索主路
        logger.warning("向量近邻查询失败（融合退纯词法）: %s", type(exc).__name__)
        return None
    return [
        {
            "asset_id": asset_id,
            "version_no": version_no,
            "chunk": redact(chunk),
            "source_kind": source_kind,
            "cos": float(cos_sim),
        }
        for asset_id, version_no, chunk, source_kind, cos_sim in rows
        if not (drop_reviews and source_kind == "review_import")
    ]


def fuse_dense_sparse(
    lex_hits: list[dict[str, Any]],
    vec_hits: list[dict[str, Any]] | None,
    *,
    vec_weight: float = VECTOR_WEIGHT,
) -> list[dict[str, Any]]:
    """加权线性融合（矩阵 C 形态，纯函数便于单测）：词法分 per-query min-max
    归一 + vec_weight × cosine。

    - 归一域 = lex_hits 全集的 score（79 刀现役综合分：词法×stale×亲和——
      affinity_before，乘数在融合前原位）；max==min（含单块命中）时全取 1.0；
    - 词法命中块无向量（NULL 块/未进向量 top-N）cos 项为 0——fail-open 只
      加分不排除；向量补召回块（不在词法命中集）词法项为 0；
    - vec_hits=None/空 = 纯词法：归一分排序（min-max 单调，名次与词法分一致，
      仅并列破平次序可能因归一化持平——同分块归一同值，稳定键不变）。
    """
    if not lex_hits:
        return []
    if not vec_hits:
        vec_hits = []
    raw = [hit["score"] for hit in lex_hits]
    lo, hi = min(raw), max(raw)
    cos_of = {(hit["asset_id"], hit["version_no"], hit["chunk"]): hit["cos"] for hit in vec_hits}
    pool: dict[tuple[int, int, str], dict[str, Any]] = {}
    for hit in lex_hits:
        key = (hit["asset_id"], hit["version_no"], hit["chunk"])
        norm = 1.0 if hi <= lo else (hit["score"] - lo) / (hi - lo)
        pool[key] = {**hit, "score": norm + vec_weight * cos_of.get(key, 0.0)}
    for hit in vec_hits:
        key = (hit["asset_id"], hit["version_no"], hit["chunk"])
        if key not in pool:
            pool[key] = {
                "asset_id": hit["asset_id"],
                "version_no": hit["version_no"],
                "chunk": hit["chunk"],
                "score": vec_weight * hit["cos"],
            }
    return sorted(pool.values(), key=lambda hit: (-hit["score"], hit["asset_id"], hit["chunk"]))


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
    时纯字段问也有亲和，ADR 0048 已知取舍）。

    稠密稀疏融合（第 106 刀，ADR 0058，affinity_before）：词法命中集（上述
    全管线产物）的分数 per-query min-max 归一后加 ``VECTOR_WEIGHT × cosine``
    ——向量路给语义近邻加分/补召回（cos ≥ VECTOR_COS_FLOOR），**不排除任何
    词法命中**（embedding NULL 块照旧参与，fail-open）。词法空手闸：词法零
    命中时直接空手出（向量不无中生有，0018 拒答语义完全由词法路决定）。
    未配置 EMBED_API_KEY / 嵌入失败 / 向量查询失败 = 纯词法（归一化单调，
    名次逐位不变）。返回的 score 是融合分（归一词法 + 0.5×cosine）。
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
    aff_of = {asset_id: title_affinity(terms, title, idf) for asset_id, title in titles.items()}
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
    # 第 106 刀融合（ADR 0058）：词法命中集上叠加向量分（矩阵定案 C-w=0.5-
    # tau0.60-lexgate-before）。词法空手闸在此单点收口——unique 为空直接空手
    # 出，向量路不无中生有（拒答语义完全由词法路决定，refusal 组 26/30 词法
    # 零命中的照旧拒答）。未配置 key/云调用失败时 dense_candidates 返回 None，
    # fuse_dense_sparse 退纯词法（归一化单调保序，名次与词法分排序逐位一致）。
    lex_hits = list(unique.values())
    if not lex_hits:
        return []
    vec_hits = dense_candidates(db, query, drop_reviews=drop_reviews)
    fused = fuse_dense_sparse(lex_hits, vec_hits)
    # 第 123 刀字段行定向（保序重排，见 _field_directed 注释）
    return _field_directed(fused, terms)[:top_k]
