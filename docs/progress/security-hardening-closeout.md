# 工程第 10 刀 closeout：顾客通道安全面收口（feat/security-hardening）

日期：2026-09-08。上游：第 8 刀 closeout 遗留三项 + 其两轴评审 (c) 项。短对齐：`.scratch/security-hardening/spec.md`。基线=main（PR #12 合并后）。

## 交付（三项安全语义收口，零新功能面）

1. **XFF 信任模式**（`CUSTOMER_TRUST_PROXY`，默认 False=直连 fail-closed）：直连只信 TCP 对端、完全忽略自报 XFF（第 8 刀遗留：直连部署下换头即可绕过 IP 闸）；True=反代模式信 XFF 第一跳（部署者负责反代强制覆盖头，README 两模式表）。首跳空白回退对端（评审处置）。
2. **闸序重排**：IP 闸（前置，狂刷不碰库裁决保持）→ 401 统一 → **会话闸后置到令牌鉴权之后**（无效令牌不再替真顾客会话耗发问配额）→ 409 → 422 → 引擎。`check_ask` 拆 `check_ask_ip`/`check_ask_session`。
3. **存在性收敛**：会话不存在与令牌无效统一 401「会话不存在或令牌无效」（原 404/401 双态让自增 session id 可探测），`WWW-Authenticate: Bearer` 保持。

## Owner 验收（全过）

- 回归：`/customer` 真模型「净含量为 500ml。」+ `A-0003 · v1` 零变化。
- eval 三连：换 5 个伪造 XFF 建会话第 5 个 429+Retry-After（同 IP 记账，换头无效）；坏令牌 12 连打全 401 后**好令牌同会话 200**；坏令牌与不存在会话同 401 同文案。

## 两轴评审与处置

- Standards：**零硬违规**；CONTEXT 推进段滞后由本 closeout 补上。judgement call 记债务：限流测试样板 ×5 收敛、client_ip 四层链（灰区）。
- Spec：无 Must 缺失。实修：trust 模式空白首跳回退对端（空串 key 会让畸形请求共享一本账）+ 直连无头/空白首跳直接单测。核实无偏差项：validator 空串→False 三态殊途同归、IP 闸拒后不查库、409/422 记账口径与 spec 序一致、新语义均有测试锁。

## 工程插曲（子代理处理正确，记录在案）

compose `${CUSTOMER_TRUST_PROXY:-}` 空串会让 pydantic bool 字段拒启动；首版 `env_ignore_empty=True` 破坏 conftest 空凭证清理机制（真 LLM 密钥险些漏进测试、3 测试失败）——改字段级 `field_validator`（空串→False）不动全局，密钥纪律守住。

## 计数（摘自命令输出）

- 仓库根无 DB：`147 passed, 52 skipped in 6.36s`
- 带 `SUITE_TEST_DATABASE_URL` 全量：`199 passed in 21.75s`
- `ruff check apps packages`：All checks passed
- web 零改动（build/lint 沿第 9 刀绿态；本刀无前端 diff）

## 遗留

- 限流测试样板收敛、client_ip settings 注入形态（灰区债务）
- 分布式限流/令牌过期吊销维持 Out（多副本属部署演进）

## 下一刀

回流增强（调研 §6 候选 2：会话回流后 LLM 自动抽 QA 草稿→人洗→发布，与 CSV 通道互补——「初始知识进来」与「对话变知识」）或按计划者方向；审计刀 2 计数线（约第十一刀）已近。
