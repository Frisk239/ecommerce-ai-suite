# 工程第 16 刀 closeout：还债刀（feat/debt-1）

日期：2026-09-08。上刀 intake：`docs/progress/audit-3-intake.md`（通过）。短对齐：`.scratch/debt-1/spec.md`（施工单=审计刀 3 P1 簇）。基线=`origin/main`（8b4d5a9，PR #20 合并后）。

## 交付（审计刀 3 P1 六项 + 顺手，零新功能面）

1. **跨循环 LLM 客户端（P1#1）**：`llm.py` 按运行中事件循环懒建 AsyncOpenAI（WeakKeyDictionary），主循环（顾客 stream）与线程池一次性循环（QA 抽取 complete_chat）各建各用——混跑不再 "attached to a different loop"；混跑钉测（替身模拟 loop 亲和，旧单例必炸）。
2. **回流 LLM 前释放事务（P1#2）**：`register_asset` 登记行 flush+commit 后才跑机洗（LLM ≤20s 不 idle-in-transaction，对齐第 11 刀纪律）；retry 端点同款 pre-wash commit；两路钉测（complete_chat 时刻 in_transaction=False）。
3. **库存词表收窄+商品双前置（P1#3）**：词表收敛 `有货|没货|无货|缺货`（去「库存/现货/剩」）；分派改「词表命中且商品 LCS 匹配成功」才走工具，词表命中无商品名**回检索**（拒答留缺口，归宿对齐词条）——已发布库存政策文档不再被吞；ADR 0037 补修订段（推翻旧「误伤代价低」论证）。
4. **QA 块保位（P1#5）**：发布切块 confirmed QA 块先于转写正文块，`[:200]` 截断先丢正文——长会话人洗成果不再静默丢失；250 轮截断钉测。
5. **工具吞异常加日志（P1#6）**：order/stock 两工具 5 处 `logger.exception`，对外行为不变（仍吞→handoff），logger 打桩钉调用。
6. **回写（P1#8+P2）**：ADR 0027 与 CONTEXT 评测集词条补工具期望形状；chat_engine 过期注释、ADR 0037 键名、`_inherit_confirmed` 数组值 inherited 标签（web 兼容零改动）。

## Owner 验收

门禁全绿（见计数）；浏览器抽查（顾客通道）：「钛钢保温杯有货吗」仍走工具（42 件）；「保温杯还剩多少毫升」**走检索**——真模型引用 `A-0003 · v1`+`A-0009 · v1` 诚实回答「证据未提供容量规格」（审计刀 3 的误伤场景消除）；「库存政策是什么」走检索拒答转人工（政策文档发布后可命中，集成钉测）。

## 两轴评审与处置（`docs/progress/debt-1-review.md`）

零硬违规、七条 Must 全落地零越位；实修三处（retry 钉测、两段式 docstring 崩溃窗口限定、llm 注释口径）；被更既有断言逐条核为对称收紧非放松。记债：分派谓词可提纯函数；词表命中无匹配时全表扫（规模可忽略）。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=327 passed=236 failed/errored=0 skipped=91`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=327 passed=327 failed/errored=0 skipped=0`（基线 316 → 327 只增）
- web：无产品改动

## 遗留

- 一次性 loop 客户端 fd churn 面（finalizer 关 socket，记录在案）；密钥热轮换需重启（与修复前同口径）。
- 审计刀 3 P1#4 打码（进素材刀）、P1#7 素材/切片/考核三模块（排期 17–19 刀）、共享缝（第三工具时）。

## 下一刀

刀计数：第 16 刀（审计后第 1 刀）。**第 17 刀=素材中心**（audit-3 排期：任务 0012+质检过线登记 0029+material_generated 来源兑现+打码小面）；审计刀 4 于第 20 刀后触发。CONTEXT 推进句已回写。
