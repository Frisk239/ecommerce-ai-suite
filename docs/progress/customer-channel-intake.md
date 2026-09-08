# 工程第 8 刀 intake：顾客通道（feat/customer-channel）

日期：2026-09-08。Intake Owner：视觉收尾刀 Owner（接手）。

## 核验

- **Merge**：`feat/customer-channel` 未合 main（stack 基线 = PR #9 合并后，closeout 自述一致）；不动，等用户合并。
- **Evidence 复现**：`SUITE_TEST_DATABASE_URL=…/suite_test uv run pytest` → **166 passed in 20.09s**，与 closeout 计数一致。
- **Spec 抽查**：closeout 三件套（closeout 文档 / slices 第 8 刀节 / CONTEXT 推进句）均在 `fb68934`；顾客页 `/customer` 本 session 已由 intake Owner 浏览器亲验（会话签发、消息流、引用只读芯片在跑）。
- **Safety**：工作区仅 `reference/README.md` 改动 + `docs/research/demo-to-product-gaps.md` 新增（间隙调研产物，随下一刀入库）；无密钥/运行时产物。

## Verdict：**通过**

债务照单接收（进后续刀议题，非本刀）：

- 安全面：XFF 自报伪造（直连模式只信 remote addr 待做）；限流先于鉴权（无效令牌耗配额）；404 先于 401 存在性探测。
- 行为债：ServicePage 失败占位表现与顾客页不一致（行为改动，视觉刀的零行为闸外）。
- 工程债：askService/askCustomer 分发闭包、UiMessage 字面量工厂、ACTIVE 常量集中、AbortController 即弃。
