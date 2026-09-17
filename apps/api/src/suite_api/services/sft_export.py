"""SFT 数据集导出装配（第 97 刀/ADR 0054）：只出已发布对话的人确认问答对。

数据源与形状（治理语义，见 ADR 0054）：

- 数据源=「已发布对话资产」的**当前指针版**（kind=dialogue、status=published、
  未废弃）的 ``confirmed qa_pairs``——**只出人洗确认过的问答对**：微调数据必须
  人确认，AI 抽的草稿（extracted）不直接出口（同 0010「索引只认 confirmed」的
  口径：未经人确认的模型产物不在生效面出现）。待人洗/已接入/未发布对话不出现；
  已发布但 confirmed qa_pairs 为空数组（人确认过「没有 QA」）的资产同样零条。
- 每条样本 alpaca 三键 + meta 血缘：``{"instruction": q, "output": a, "meta":
  {asset_id, version_no, source_kind, title}}``——外部训练者凭 asset_id+version_no
  可追回每条数据的来源资产、版本字节与清洗记录（audit_log 的 confirm/publish
  事件、血缘视图）。**产品不做训练**：导出止步于数据包，训练属外部。
- 文件头：首行 ``# {json}``（generated_at/exported_by/asset_count/sample_count/
  license_note）。JSONL 规范没有注释行——本仓约定**首行以 ``#`` 起始即元数据头，
  消费端跳过 ``#`` 行**（约定写进 ADR 0054，不引旁的容器格式）。

出口必掩（ADR 0038）：confirmed q/a 与 title 落库时已掩，出口侧再过一道
``redact``（幂等，防历史脏行；title 兜底同 MCP export 先例——对话资产
title=回流首问摘要在源头已掩，出口统一收口）。

与 MCP ``export_published`` 的分工（ADR 0054）：那是连接层的**正文数据包**
（含文档/图片/切片全种类）；本服务是治理台的**微调数据集**（只对话、只问答
对、逐条血缘）。微调集导出**不进 MCP**（「恰四工具」断言不动，0020）。
"""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion
from suite_api.services.machine_wash import QA_FIELD, redact

LICENSE_NOTE = "内容经人工确认（confirmed qa_pairs）；本产品不做训练"

# 文件名日期段口径：UTC 日（与 generated_at 同时区，不自相矛盾）
FILENAME_DATE_FORMAT = "%Y%m%d"


def _confirmed_qa_pairs(version: AssetVersion) -> list[dict[str, str]]:
    """当前指针版的 confirmed qa_pairs（宽容读取，不改写形状）。

    confirmed 落库前都过 validate_qa_pairs（人洗 PATCH）——正常路径形状有保证；
    这里对历史/异构行防御：条目不是 dict、q/a 不是非空字符串的项**跳过不出口**
    （不抛错带崩整份导出、不把坏形状原样发给外部训练者）。继承自旧版的
    inherited 条目形状相同，照常出口。
    """
    entry = (version.confirmed_fields or {}).get(QA_FIELD)
    if not isinstance(entry, dict):
        return []
    value = entry.get("value")
    if not isinstance(value, list):
        return []
    return [
        {"q": item["q"], "a": item["a"]}
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("q"), str)
        and isinstance(item.get("a"), str)
        and item["q"].strip()
        and item["a"].strip()
    ]


def load_sft_samples(session: Session) -> list[dict[str, Any]]:
    """全量装配 SFT 样本：已发布对话（当前指针版）的 confirmed 问答对逐条成行。

    查询形态同 MCP export_published（ADR 0041 留痕先例的取数面）：status=
    published + 指针 join，已废弃过滤；按 Asset.id 升序、同资产内按 confirmed
    顺序。读取只碰库内字段（qa_pairs 是版本字段，不读对象字节）。
    """
    rows = session.execute(
        select(Asset, AssetVersion)
        .join(AssetVersion, Asset.current_published_version_id == AssetVersion.id)
        .where(
            Asset.kind == "dialogue",
            Asset.status == "published",
            Asset.discarded_at.is_(None),
        )
        .order_by(Asset.id)
    ).all()
    samples: list[dict[str, Any]] = []
    for asset, version in rows:
        for pair in _confirmed_qa_pairs(version):
            samples.append(
                {
                    "instruction": redact(pair["q"]),
                    "output": redact(pair["a"]),
                    "meta": {
                        "asset_id": asset.id,
                        "version_no": version.version_no,
                        "source_kind": asset.source_kind,
                        "title": redact(asset.title) if asset.title else asset.title,
                    },
                }
            )
    return samples


def export_lineages(samples: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """从样本提取入选资产的 (asset_id, version_no) 去重序对（audit 留痕用）。

    样本 meta 已带全血缘；dict.fromkeys 保序去重（同 load_sft_samples 的
    Asset.id 升序），空数据集得空列表——「没有资产离开系统就没有留痕行」
    （ADR 0041 ``if exported`` 同口径）。
    """
    return list(dict.fromkeys((s["meta"]["asset_id"], s["meta"]["version_no"]) for s in samples))


def build_sft_jsonl(*, exported_by: str, samples: list[dict[str, Any]], now: datetime) -> str:
    """拼 JSONL 文本：首行 ``#`` 头 + 每样本一行。ensure_ascii=False（中文可读，
    文件即给人/给外部训练者的数据包，不转义成 \\uXXXX 提高可核对性）。"""
    header = {
        "generated_at": now.isoformat(),
        "exported_by": exported_by,
        "asset_count": len(export_lineages(samples)),
        "sample_count": len(samples),
        "license_note": LICENSE_NOTE,
    }
    lines = [f"# {json.dumps(header, ensure_ascii=False)}"]
    lines.extend(json.dumps(s, ensure_ascii=False) for s in samples)
    return "\n".join(lines) + "\n"


def export_filename(now: datetime) -> str:
    """附件名：sft-dataset-YYYYMMDD.jsonl（UTC 日）。"""
    return f"sft-dataset-{now.strftime(FILENAME_DATE_FORMAT)}.jsonl"


def utc_now() -> datetime:
    """组装时刻（UTC，带 tz）——抽成函数便于测试注入固定时钟。"""
    return datetime.now(UTC)
