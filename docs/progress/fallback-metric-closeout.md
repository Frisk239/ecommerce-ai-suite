# 第 63 刀 closeout：`fallback_reason` 进指标（闸回退率可观测）

日期：2026-09-11。依据：审计刀 12 建议 3 + 审计刀 7 起记债。**无表无迁移、不动引擎**
（observability 一个模块 + 测试 + README 一行）。

## 交付

| 件 | 交付 |
| --- | --- |
| 指标 | `chat_fallbacks_total{channel, reason}`（channel=customer/operator 同 `chat_requests_total`；reason=coverage（忠实度闸降级）/ no_coverage（证据未覆盖按拒答收口）/ **other（防御位）**） |
| 记账点 | `record_chat_request` 内：`fallback_reason` 非空即计数（**None 不计**——普通厂商失败不是闸在回退）；两条 ask 路由既有调用点零改动 |
| 基数纪律 | 未知 reason 归一为 `other`（不进标签）；标签集合进契约测试 `{channel, reason}` |
| README | 业务指标清单五个 → 六个 |

## 验收

- 单测（真 `/metrics` 端点 + 真计数链）：`coverage`/`no_coverage` 两枚正向钉子（customer/operator 各一）；
  普通回答（`fallback_reason=None`）**不进**闸回退计数；未知值落 `other`；
  `# TYPE chat_fallbacks_total` 出现在 `/metrics` 文本里（带 token 的真 app 真端点）；
  标签集合 `{channel, reason}` 契约。
- 集成 **998 → 1000 passed / 0 failed / 0 skipped**；`uv run ruff check apps packages scripts` 全过；前端未改。
- 演示栈（无 `METRICS_TOKEN`）`/metrics` 照旧 401——不为本刀开闸。

## 诚实披露

- **闸回退率本身没有历史数据可对比**：指标是新增的，第 58 刀之前发生过多少次闸回退
  已不可考（此前只有日志）。指标从本刀起累计。
- **增量口径**：`chat_requests_total` 的 `generated=false` 仍含厂商失败降级与工具/
  目录回答——闸回退率要看 `chat_fallbacks_total / chat_requests_total`，不是
  `generated=false` 的占比。README 与指标 help 文本写清了取值含义。
- 单测用 `_Outcome` 替身直调 `record_chat_request`（与第 47 刀 `chat_requests_total`
  同一口径）——引擎→outcome 的 `fallback_reason` 赋值钉子在第 40/58 刀已有，
  本刀不重复。

## 评审处置（两轴独立评审）

- Standards 轴：**无 P0/P1**，P2×4 全实修——模块头有界标签清单补 `reason`（含存量漏的
  result/score）；README 词条补 `other` 防御位与**速率公式**（并把旧词条「顺带给出回退率」
  改成「模板回退率」，两种回退率点名区分）；「None 不计」负例改**指标族全样本求和**
  （collect()，陌生标签值的坏实现也抓得住）+ 补**分母共增钉子**（带闸回退的结局也必须
  记一次 chat_requests_total）；「分支不存在」属评审时序（文档先于提交），提交即消。
- Spec 轴：**无 P0/P1/P2**，P3×3——`routes/metrics.py` 模块 docstring「三个自定义」订正
  为六个；测试密闭性小口子（同上已修）；集成级钉子缺位（单测替身直调，与第 47 刀
  `chat_requests_total` 同口径，已有披露维持）。Spec 轴另确认：引擎侧 `fallback_reason`
  赋值点全仓仅一处、值域封闭；普通厂商失败路径确证为 None（LLMNotConfigured 是
  LLMError 子类 → generated=None → 两闸条件互斥）；MCP 面无 run_ask 旁路。

## 后续

- 第 64 刀起继续按排期；**审计刀 13 于第 65 刀后**（覆盖第 61–65 刀）。
- 待 Owner 裁决：工作队列默认视角、评分可改、分币种报价。
