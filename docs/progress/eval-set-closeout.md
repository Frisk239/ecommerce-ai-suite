# 工程第 15 刀 closeout：评测集（feat/eval-set）

日期：2026-09-08。上刀 intake：`docs/progress/stock-tool-intake.md`（通过）。短对齐：`.scratch/eval-set/spec.md`（不新开 ADR，0027「v1 可复现记录」覆盖）。基线=`origin/main`（033fbe2，PR #18 合并后）。

## 交付（golden conversations + policy edges 进 CI，ADR 0027 落地）

1. **`apps/api/evals/golden.json`**：13 条 golden case，三种期望形状——`cite_asset_title`（按标题锚，不锚 id——测试库 id 漂移）/ `refuse` / `tool`（含可选 handoff 标记）。覆盖：规格引用、退货政策引用、QA 对话命中（回流资产）、拒答、订单命中（含小写归一）/查无转人工、库存有货/无货/未命中、三个 policy edge（无单号订单问法→拒答不冒充工具且 tool=None、越权「忽略规则」问法→拒答、单号+库存词→订单优先）。
2. **`apps/api/tests/test_eval_set.py`**：schema 自检（缺字段/非法 expect/重复 id 指名 case id）+ 覆盖谱自检 + module 级 anchors fixture（发布两文档+回流 QA 对话资产，标题锚一致）+ 参数化 runner 直调 `run_ask`（不 mock 引擎/工具/检索），断言只锚 kind/handoff/citations/tool——空 key 全可复现，不比模型文案。
3. **README**：评测集段（加 case 指引、标题锚口径、随套跑即回归防线）。
4. 产品代码零改动、零新依赖、无表无端点（词条 _Avoid_ 评测台当中台对象守住）。

## Owner 验收

ruff 全过；无 DB `316 收集 / 227 passed / 0 failed / 89 skipped`；评测集模块带 DB 单跑 `15/15`；带 DB 全量 `316 passed / 0 failed`（基线 301 → 316 只增）。演示路径=跑评测集命令本身（开发者视角），Owner 亲跑。

## 两轴评审与处置（`docs/progress/eval-set-review.md`）

零硬违规、四条 Must 全达标、零越位；零代码修改。记债务：expect 形状分派两遍、`_TOOL_NAMES` 镜像、case id 命名约定探测、摘要格式锚、README「CI 位」措辞。

## 遗留

- 上述五条轻债；真实 LLM 评测（有 key 时跑模型答対评分）不做（空 key 可复现优先）；批量评分统计报表不做。
- 仓库无 CI workflow 文件（本地/会话内跑门禁；上 GitHub Actions 留部署刀）。

## 下一刀

刀计数：本刀第 15，**计数线到——下一刀=审计刀 3**（近五刀=12 回流增强/13 订单/14 库存/15 评测集，审计范围自 PR #16 起的 main 增量+全仓复核）。审计候看输入（各刀 review/closeout 已记）：工具共享缝（_run_order_ask/_run_stock_ask 45 行重复）、「剩」字词表误伤面、第 12 刀债务（requestText/_version_or_404/测试替身）、第 13 刀债务（ToolCallRecord 类型/三态判别/kind 注释集中）、素材/切片/考核三模块未进队的功能缺口。CONTEXT 推进句已回写。
