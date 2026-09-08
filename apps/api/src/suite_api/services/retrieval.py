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
- retrieve 只查「当前已发布版本」的 chunks：join assets 的
  current_published_version_id 指针（0006）。待人洗/已接入不出现由 join
  语义保证而非事后过滤（0017：索引=已发布的派生视图，指针前移命中集合
  跟着走）。空查询/纯停用词 -> 空。
"""

import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, RetrievalChunk
from suite_api.services.machine_wash import QA_FIELD
from suite_platform.storage import ObjectStorage

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


def index_chunks_for_version(
    storage: ObjectStorage,
    object_key: str,
    kind: str,
    confirmed_fields: dict[str, Any],
) -> list[str]:
    """发布事务用的切块入口：版本字节切块 + 确认字段块（seq 续排）。

    确认字段块的目的：机洗没抽到/抽散的字段，其「字段名：值」仍可被检索
    （问「净含量」命中字段块）；只用 confirmed（写回口径同 0010：confirmed
    才是操作者背书的值），弃权/未确认不进。dialogue 的确认字段是 qa_pairs
    结构化数组，每对成一块「问：…/答：…」（第 12 刀）。行对象由调用方
    （发布事务）按 (asset_id, version_no, seq) 落库。
    """
    from suite_api.services.publishing import confirmed_value

    chunks = chunk_text(read_index_text(storage, object_key), kind)
    for field in sorted(confirmed_fields):
        if field == QA_FIELD:
            chunks.extend(qa_pair_chunks(confirmed_fields))
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


# ---------- 检索（只查当前已发布版本，join 保证） ----------


def retrieve(db: Session, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
    """检索当前已发布版本的切块，按分数降序返回 [{asset_id, version_no, chunk, score}]。

    候选集 SQL：retrieval_chunks join asset_versions join assets，其中
    asset_versions.id == assets.current_published_version_id 且
    (chunk.asset_id, chunk.version_no) == (asset_versions.asset_id, version_no)。
    待人洗/已接入资产的指针为 NULL，join 天然不出现（0004/0017：由 join 语义
    保证而非事后过滤）；发布新版指针前移后，旧版 chunk 因 version_no 不再
    匹配指针版本而自动出榜（派生视图语义）。
    """
    terms = query_terms(query)
    if not terms:
        return []
    rows = db.execute(
        select(RetrievalChunk.asset_id, RetrievalChunk.version_no, RetrievalChunk.chunk)
        .join(
            AssetVersion,
            (AssetVersion.asset_id == RetrievalChunk.asset_id)
            & (AssetVersion.version_no == RetrievalChunk.version_no),
        )
        .join(
            Asset,
            (Asset.id == AssetVersion.asset_id)
            & (Asset.current_published_version_id == AssetVersion.id)
            & (Asset.status == "published"),
        )
        .order_by(RetrievalChunk.id)
        .limit(_MAX_CANDIDATE_ROWS)
    ).all()
    scored = [
        {"asset_id": asset_id, "version_no": version_no, "chunk": chunk, "score": score}
        for asset_id, version_no, chunk in rows
        if (score := score_chunk(terms, chunk)) > 0.0
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
