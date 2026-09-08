# 工程第 25 刀两轴评审：前端收口刀（feat/frontend-cleanup）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（`d9dfb2c`）。Spec：`.scratch/frontend-cleanup/spec.md`。两轴合并轻评审。

**净，可合。** apps/api 零 diff；toUiMessage 13 字段单点、4 处内联删净；useAskStream 两页共享（gap_id 等价/事件序逐行对齐/stop-reset 语义保留）；AssetAnchorChip 三处替换（不复用 CitationChip 理由注释）；ActionError 9 处统一（三档 DOM 冻结逐字一致）；Out 零越界。

P2 记债：顾客页 reset 现主动 abort 在途流（旧为自然结束——网络侧微差 UI 不可见，改良）；操作者页 streaming 独立 state 致锁输入毫秒级延长。

## 计数（Owner 复跑）

- pytest：`tests=491 passed=368 failed/errored=0 skipped=123`（基线不变，api 零 diff）
- web：`tsc --noEmit` 0 错、`npm run build` 过
- Owner 浏览器抽查（重构后行为等价）：顾客通道「SO-1001」问答工具条+已发货照常（useAskStream）；考核页题源锚 `A-0009 · v1` 芯片渲染可点（AssetAnchorChip）
