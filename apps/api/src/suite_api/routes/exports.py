"""导出路由（第 97 刀/ADR 0054）：微调数据集（SFT 形态）经治理台导出。

单端点 ``POST /api/exports/sft``：操作者 cookie 鉴权（0016），响应=JSONL 附件
流（``attachment; filename=sft-dataset-YYYYMMDD.jsonl``）。**本产品不做训练**：
导出止步于带血缘的数据包，训练属外部（ADR 0028/0054）。

与既有两个「导出」的分工（ADR 0054）：

- MCP ``export_published``（0041）：连接层正文数据包（全种类、含字节正文），
  operator 归系统行「mcp」；本端点是治理台动作，audit 用**操作者本人 id**——
  留痕形状复用 0041 先例（每份入选资产一行，含当时版本号；空数据集零行）。
- 版本正文端点：操作者面单版原文回放；本端点面向**外部训练者**，故出口统一
  过 redact（0038「出口必掩」——confirmed 落库已掩，这里幂等兜底防历史脏行）。

微调集导出**不进 MCP**：「恰七工具」断言不动（0020/0057；test_mcp_evidence 钉着）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db
from suite_api.models import AuditLog, Operator
from suite_api.services.sft_export import (
    build_sft_jsonl,
    export_filename,
    export_lineages,
    load_sft_samples,
    utc_now,
)

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.post("/sft")
def export_sft(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> Response:
    """导出微调数据集（SFT）：已发布对话资产的人确认问答对，JSONL 附件。

    - 数据源：kind=dialogue、status=published、未废弃资产的**当前指针版**
      ``confirmed qa_pairs``（只用 confirmed：AI 抽的草稿不直接出口，同 0010
      的生效面口径）；alpaca 三键 + ``meta: {asset_id, version_no, source_kind,
      title}`` 逐条血缘——外部训练者可追回来源与清洗记录。
    - 文件头：首行 ``# {json}``（generated_at/exported_by/asset_count/
      sample_count/license_note）。JSONL 注释行是本仓约定（ADR 0054）：消费端
      跳过 ``#`` 起始的行。
    - 空数据集（无已发布对话的 confirmed 问答对）不是错误：200 + 只有头的文件，
      两个 count 如实为 0。
    - audit 留痕：action='export_sft'，operator=登录者本人（治理台有真人身份，
      不走 0041 的系统行「mcp」）；每份入选资产一行（asset_id+version_no），
      只记元数据——问答对正文不落留痕（同 0041）。血缘「导出」环按 action
      分派（EXPORT_ACTION 只认 'export'），SFT 导出不混入连接层导出口径；
      资产时间线（versions 审计流）里 action 如实可见。
    """
    samples = load_sft_samples(db)
    now = utc_now()
    body = build_sft_jsonl(exported_by=operator.username, samples=samples, now=now)
    lineages = export_lineages(samples)
    if lineages:  # 空数据集零留痕行（0041「if exported」同口径：没有数据离开）
        db.add_all(
            AuditLog(
                operator_id=operator.id,
                asset_id=asset_id,
                version_no=version_no,
                action="export_sft",
            )
            for asset_id, version_no in lineages
        )
        db.commit()
    return Response(
        content=body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename={export_filename(now)}"},
    )
