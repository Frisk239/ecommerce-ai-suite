# ADR 0054：微调数据集导出（SFT 只出 confirmed / 治理台非 MCP / 产品不训练 / DPO 待数据积累）

- 日期：2026-09-16（第 97 刀）
- 状态：接受
- 相关：ADR 0005（发布留痕）、0010（confirmed 才进索引）、0020（连接层只读已发布、恰四工具）、0028（不做微调、推理只走厂商 Chat API）、0035（LLM 草稿与人洗）、0038（字节不动、出口必掩）、0041（export 留痕与系统操作者「mcp」）；词条「连接层」「已发布」；roadmap `docs/roadmap-system-completion.md` 第 97 刀

## 背景

「微调数据供给」是懂王口径 8+1 项之一，北极星定形为**数据集导出**——产品仍不训练（ADR 0028 自始）。此前唯一的导出面是 MCP `export_published`（0041）：正文数据包，全种类、含字节原文，operator 归系统行「mcp」。它回答不了两件事：

1. **形态**：外部训练者要的是 SFT 样本（instruction/output），不是资产正文包；
2. **治理语义**：微调数据的每一条都必须**人确认过**——AI 从转写抽的问答对是草稿（0035），草稿直接出口等于「机器产出未经治理就进了训练语料」，与 0010「索引只认 confirmed」的生效面口径相悖。

同时词汇表（CONTEXT 连接层词条 Avoid）还挂着「把导出叫做微调集」——本刀起产品里**真的有**微调集形态了，词条要随刀改口（避免词汇表与实现互相说谎）。

## 决定

### 1. 单端点 `POST /api/exports/sft`，治理台动作、非 MCP

- 操作者 cookie 鉴权（0016 口径，未登录 401）；响应=JSONL 附件流
  （`attachment; filename=sft-dataset-YYYYMMDD.jsonl`，UTC 日）。
- **不进 MCP**：连接层「恰四工具」断言不动（0020；test_mcp_evidence 钉着）。
  理由：微调集是**运营/治理动作**的产物（依赖人洗确认这个前置），不是外部
  Agent 的读权威面；外部 Agent 要读已发布知识，走 `search_published`/
  `get_asset` 即可。词汇表连接层词条相应补「微调集导出走治理台，不经连接层」。
- 不做导出历史/管理面：audit_log 就是唯一事实，`GET /api/audit?assetId=` 可查。

### 2. 数据源：只出已发布对话资产的 confirmed qa_pairs

- 查询形态同 0041 先例：`kind='dialogue' AND status='published' AND
  discarded_at IS NULL`，join 当前指针版（`current_published_version_id`）。
- **只用 confirmed**（定案，弃「confirmed 优先、extracted 次之」的回落）：
  微调数据必须人确认——治理语义同 0010（未经人确认的模型产物不在生效面
  出现）。待人洗/已接入/未发布对话不出现；已发布但人确认「没有 QA」（空
  数组）的资产零条。
- 每条样本 alpaca 三键 + meta 血缘：

  ```json
  {"instruction": q, "output": a,
   "meta": {"asset_id": 12, "version_no": 2, "source_kind": "session_writeback", "title": "…"}}
  ```

  外部训练者凭 `asset_id+version_no` 可追回来源资产、该版字节与清洗记录
  （audit_log 的 confirm/publish、血缘视图）——**可溯源微调数据集**是本刀的
  差异点。样本只读库内字段（qa_pairs 是版本字段），不读对象字节。
- 出口必掩（0038）：confirmed q/a 与 title 落库时已掩，出口侧再过 `redact`
  （幂等兜底防历史脏行；title 兜底同 MCP export 先例）。

### 3. 文件头约定：JSONL 首行 `# {json}`

JSONL 规范没有注释行。本仓约定：**首行以 `#` 起始即元数据头**，消费端跳过
`#` 起始的行。头字段固定五个：

```json
# {"generated_at": "2026-09-16T…+00:00", "exported_by": "operator",
#   "asset_count": 3, "sample_count": 7,
#   "license_note": "内容经人工确认（confirmed qa_pairs）；本产品不做训练"}
```

- `exported_by`=登录操作者 username；`asset_count`=入选资产数（去重）；
  `sample_count`=样本行数。
- 空数据集**不是错误**：200 + 只有头的文件，两个 count 如实为 0（导出动作
  本身没有数据离开系统）。

### 4. audit 留痕：action='export_sft'，operator=登录者本人

- 留痕形状复用 0041 先例（每份入选资产一行，含当时版本号，正文不落留痕），
  但 operator 归属不同：连接层调用方只有 Bearer token、归系统行「mcp」
  （0041）；治理台有真人身份——**用登录者本人 id**。
- 空数据集零留痕行（同 0041 `if exported` 口径：没有资产离开）。
- 血缘视图按 action 分派（`WRITEBACK_ACTIONS`、`EXPORT_ACTION` 均不含
  export_sft）：SFT 导出不混入写回、也不冒充连接层导出；资产审计**时间线**
  里 action='export_sft' 如实可见。

### 5. 产品不做训练；DPO 待数据积累

- 训练属外部：导出止步于数据包（ADR 0028 不动——不训练、不接厂商
  Fine-tune 作业、不在客服里切微调底座）。
- **DPO 不做**：负反馈复审队列（顾客「没有帮助」推进的引用资料复审）是潜在
  rejected 源，但 (chosen, rejected) 配对量未撑起——列「待数据积累」，不预建
  空格式。

## 动机

「微调数据供给」的最小真形态不是训练，而是**把治理成果变成外部可用的训练数据，且每条可追责**。只出 confirmed 的裁剪与发布闸门同构：顾客面前不出现未经人确认的模型文本（0010），训练语料里同样不出现。血缘逐条内嵌（meta 三键），让「这条数据从哪来、谁洗过、哪一版」在数据离开产品之后仍然成立——这是数据中台口径对「微调」一词的贡献，而不是把模型训练搬进产品。

## 后面

- **Out**：DPO 形态（待负反馈配对数据积累）；MCP 侧 SFT 工具（恰四工具不动）；导出历史/管理面（audit 即事实）；按商品/时间窗筛选导出。
- **Debt**：文件头是约定不是规范——外部消费者若用严格 JSONL 解析器需自行跳 `#` 行（头里 license_note 已声明口径）。
- 测试：`apps/api/tests/test_exports_sft.py`（401/形状/血缘钉/未发布与草稿不出现/空数据集/audit 留痕，真 PG）。
