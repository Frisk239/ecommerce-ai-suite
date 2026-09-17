# 第 97 刀 closeout：SFT 数据集导出（微调数据供给落地）

日期：2026-09-16。分支 `feat/sft-export-97`（stacked 于 `feat/usage2-demo-96`）。

## 交付

1. **`POST /api/exports/sft`**（routes/exports.py + services/sft_export.py）：操作者 cookie 鉴权；JSONL attachment（首行 `#` 头注释五字段：generated_at/asset_count/sample_count/exported_by/license_note）；每条 `{"instruction","output","meta":{asset_id,version_no,source_kind,title}}`——**逐条血缘可追回**；audit `export_sft` 用登录者本人 id（空导出零留痕=ADR 0041 同口径）；q/a/title 出口过 redact 幂等兜底。
2. **数据源定案**：只出已发布 dialogue 的 **confirmed** qa_pairs（AI 草稿不出口——人确认过的才配喂模型，治理语义）；未发布/草稿不出现（钉测）。
3. **MCP 不加工具**（「恰四」断言不动，test_mcp_evidence 19 例仍绿）。
4. **文档三处随刀修订**（goal 修订表既定）：README 导出句（区分 MCP 数据包与治理台 SFT 导出）、goal §4⑦ 末句、CONTEXT 连接层词条（Avoid 换词+新词条「微调数据集导出」）；手册幕 14 移入已实现栏。
5. **ADR 0054**：confirmed-only/治理台非 MCP/产品不做训练/DPO 待数据积累（负反馈复审队列=潜在 rejected 源）。

## 证据

- **Owner 门禁亲验**：1390/0/0/0（+5）+ ruff 全过。
- **真栈导出**：`sft-dataset-20260916.jsonl` 7 行=1 头+6 样本（4 份已发布 dialogue：A-9/A-223×3/A-224/A-225 各 v1）；**meta 血缘与库内指针逐项一致**；A-493（pending_review）未出现；audit 4 行本人留痕；无 cookie 401。
- **redact 完整性**（评审确证）：六样本 instruction/output 掩后完好。

## 评审实修（两轴子代理：均无 P0）

- 手册幕 14 头字段补全（五字段）。
- 确证：修订表字面与实际文本的偏离属正确执行（治理台动作口径）；title 兜底与 _mask_title 同形有注释声明；一次性拼装非流式（演示规模可接受，ADR 未记此取舍——记 P3 债）。

## 记债

1. SFT 导出非流式拼装（大语料时改流式——101 刀语料扩容后重估）。
2. out 导出产物不入库（演示工件，本机留档）。

## 后续

第 98 刀：自媒体内容套件（文案三模板+配图生成 IMGGEN_*+质检双闸）。
