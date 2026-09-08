# 工程第 21 刀 closeout：打码出口收口（feat/redact-exports）

日期：2026-09-08。上刀 intake：`docs/progress/audit-4-intake.md`（通过）。短对齐：`.scratch/redact-exports/spec.md`；领域裁决：ADR 0038 修订段（**版本字节不动，出口必掩**）。基线=`origin/main`（24998d3，PR #26 合并后）。零新功能面。

## 交付（审计刀 4 P0 簇七出口一揽子收口）

1. **检索→prompt**：retrieve 返回处统一掩 chunk（打分吃原文、两消费者+MCP search 同口覆盖）；build_prompts 另掩顾客问句行；模板降级随上游干净。
2. **人洗 confirmed 值**：PATCH 落库前 `_mask_confirmed_value`（字符串值+qa_pairs 每项）。
3. **coaching 三输入**：兜底题面推导处、build_score_prompt 唯一漏斗（score/rescore 同口）、落库前三字段。
4. **MCP export_published** 正文 redact。
5. **血缘引用问句样例**先掩后截（防 60 字边界拦腰）。
6. **MCP get_asset**（评审 P0）：content+extracted/confirmed 字段表 `_mask_fields_map`（幂等兜历史脏行）。
7. **title 全线**（评审 P1）：`_first_question` 源头先掩后截——回流登记 title/会话摘要/MCP/降级回答标题一次干净。
- **豁免钉死**：版本正文端点（操作者面）不打码——集成断言原文仍在。
- **小修四项**：ClipsPage「机洗切出候选」文案、CONTEXT「任务」词条漂移修正（Edit 精确替换）、ADR 0038 素材抽字段正名、「4 查询」→5 回写。

## Owner 验收

门禁全绿（458/458）；redact 模块 10/10 亲跑；本刀无新 UI 面——验收线=集成测试（发布含号对话→问句命中→prompt/回答无裸号；PATCH 含号值→索引无裸号；coaching/MCP export/get_asset/血缘/标题各出口钉测；版本正文豁免钉死）。

## 两轴评审与处置（`docs/progress/redact-exports-review.md`）

评审抓 P0×1（get_asset 第六出口）+P1×1（title 全线）——**均已实修**（`4d155a8`，+4 例钉测）；零既有断言改动；P2 记债：knowledge_gaps.question 裸落（治理台消费面，下轮 ADR 括注）。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=458 passed=344 failed/errored=0 skipped=114`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=458 passed=458 failed/errored=0 skipped=0`（基线 446 → 458 只增）

## 遗留

- knowledge_gaps.question 出口（P2，治理台语境）；打码规则扩展（身份证/地址）Out；多角色权限收紧（操作者面豁免的边界）留部署语境。
- audit-4 P1 债池不变（GIN/题库 O(N)/N+1/前端收口）。

## 下一刀

刀计数：第 21 刀（审计刀 4 后第 1 刀）。**第 22 刀=运营 Agent 最小厚切**（能力 7/7：三步轨迹+失败重试+确认投放，照原型 Ops.tsx；复用 material 服务；顺手 MCP export 留痕一行——血缘「导出」环垫底）；审计刀 5 于第 25 刀后触发。CONTEXT 推进句已回写。
