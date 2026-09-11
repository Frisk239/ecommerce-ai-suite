# 第 63 刀 intake：`fallback_reason` 进指标（闸回退率可观测）

上一刀：第 62 刀类目别名 9/9 + 库存侧纯度闸（PR #92）。本刀取审计刀 12 的
**建议 3**（「闸回退率进指标（小）：`fallback_reason` 加一个带界标签的计数器，
接上第 47 刀的观测面」），也是**审计刀 7 起的记债**。

## 现状（开刀前）

- 引擎已产 `AskOutcome.fallback_reason`（两值有界）：`coverage`（忠实度闸把生成
  降级为证据模板，ADR 0044 §二，第 40 刀）、`no_coverage`（模型自述证据未覆盖按
  拒答收口，第 58 刀）。普通厂商失败降级为 **None**（键不出场）。
- 第 47 刀的观测面有五个业务指标，其中 `chat_requests_total{channel,kind,generated}`
  的 `generated=false` 只给**模板回退率**——分不出「闸在回退」与「厂商失败降级」。
  闸回退率此前只能靠日志数行（第 47 刀 closeout 原话）。
- 记账点已在位：`record_chat_request(outcome, channel=...)` 在两条 ask 路由
  （customer/operator）各调一次，在 route 层记而非 SSE 生成器里记（懒执行问题）。

## 本刀范围（小刀）

1. `chat_fallbacks_total{channel, reason}`：`reason ∈ {coverage, no_coverage, other}`——
   **other 是防御位**（引擎将来加新 reason 而指标没跟上，宁可落 other 也不让
   陌生值进标签，基数纪律）。**None 不计**：普通厂商失败不是闸在回退，混进来
   污染闸回退率。
2. 记在 `record_chat_request` 里（两条通道天然同口径），不动引擎。
3. README 指标清单（五个→六个）。

## 明确不做

- 不加面板/告警/远端写（第 47 刀的 Out 维持）。
- 不动 `fallback_reason` 的取值与暴露白名单（审计刀 12 P1 已收口为操作者通道）。
