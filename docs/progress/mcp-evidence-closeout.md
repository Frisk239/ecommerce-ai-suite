# 工程第 38 刀 closeout：连接层协议证据（feat/mcp-evidence）——goal §6.2.5 达成

日期：2026-09-09。上刀 intake：`docs/progress/mcp-evidence-intake.md`（37 刀通过）。依据：goal §6.2.5+roadmap 第 38 刀行。基线=`origin/main`（cd393b2，PR #46）。

## 交付（答辩级协议证据）

1. **`mcp_smoke.py --evidence` 模式**：官方 SDK 全链后追加四断言结构化摘要——E0 register→未发布（status=ingested/pending_review）；E1 工具恰四且无 publish（集合相等防偷加）；E2 **未发布不进检索**（登记前后检索零差异+探针资产/标记词永不出现——对分词噪音鲁棒的设计）；E3 活状态工具不暴露（order/stock 字样反向断言，0021/0036 口径）。任一 FAIL 非零退出。Owner 修复：默认探针资产 1→3（修订流转鲁棒性）。
2. **`test_mcp_evidence.py`**：E1/E2/E3 的 pytest 版（FastMCP TestClient，独立测试库，CI 跑）。
3. README 补协议证据一行命令。

## Owner 验收

实跑 `--evidence` 两次全 PASS（exit=0）；门禁 645/645。

## 计数

- ruff 全过；无 DB `645/487/0/158`；带 DB `645/645/0/0`

## 遗留

P2×2（E0 双值口径/探针累积）；外部 IDE 现场连不要求。

## 下一刀

**第 39 刀自进化仪表**（缺口 hit_count+热度排序；last_verified/过期降权；挂缺口发布若 retrieve 仍空不标 resolved——0031 修订）→ 40 忠实度/反馈/两阶段写 → **审计刀 7**。CONTEXT 推进句已回写。
