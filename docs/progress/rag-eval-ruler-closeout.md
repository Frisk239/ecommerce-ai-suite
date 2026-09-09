# 工程第 35 刀 closeout：RAG 评测尺（feat/rag-eval-ruler）——goal §6.2.2 验收物落地

日期：2026-09-09。上刀 intake：`docs/progress/ci-gate-intake.md`（33 刀交接收口）。短对齐：`.scratch/rag-eval-ruler/spec.md`（goal §6.2.2 + roadmap 第 35 刀 + 路 C §3；0027 延伸）。基线=`origin/main`（667e155，PR #42）。

## 交付（「没有这份报告，简历不许写 RAG 质量」的尺子造出来了）

1. **`scripts/eval/generate_golden.py`**：连演示库读已发布资产（52 个）→ 固定种子 42 生成 **96 条四分布大集**（正例 40/同义 25/混淆 15/应拒答 16；拒答候选与检索同口径词法探测剔除库内已有话题 8 条）。
2. **`scripts/eval/run_eval.py`**：直调 retrieve(top-3)+compose_answer（零 LLM 依赖）——recall@1/@3、拒答率、误拒率、混淆@1；`--judge` 可选 faithfulness（空 key/LLMError 跳过）；`--report` 出 md。
3. **首份实测报告 `docs/research/rag-eval-report.md`**（数字机械粘贴，三方复跑逐位一致）：

| 分布 | 条数 | recall@1 | recall@3 | 其他 |
|---|---|---|---|---|
| 正例 | 40 | 70.0% | 75.0% | 误拒 2.5% |
| 同义改写 | 25 | **56.0%** | 64.0% | **-14pp=36 刀靶** |
| 跨商品混淆 | 15 | 60.0% | 80.0% | 混淆@1 60.0% |
| 应拒答 | 16 | — | — | **拒答率 100%** |
| 总体 | 96 | 63.7% | 72.5% | — |

4. **`services/synonyms.py`**：15 对电商同义词+`apply_synonyms`（本刀供改写器；36 刀接检索侧做 after）。
5. **CONTEXT 词条**：评测集补「评测尺」分层句。

## Owner 验收

门禁全绿（611/611）；评测双跑一致（实现方×2+Owner 复跑）；**内置浏览器（computer-use）实证**：问评测集 pos-001→引用 A-0245 · v1（expect 一致）+正确回答——线上行为与评测尺互证。

## 两轴评审（`docs/progress/rag-eval-ruler-review.md`）

净可合；P2×2（judge 列待网关稳补跑/改写模板简单）。

## 计数（摘自命令输出，junitxml 机械计数）

- ruff：All checks passed；无 DB `611/463/0/148`；带 DB `611/611/0/0`（593→611 只增）

## 遗留

judge faithfulness 数字（网关稳后 `--judge` 补跑更新报告）；OFF 同文块并列/标题不进块两已知边界记报告内。

## 下一刀

**审计刀 6（计数线到）**：三路子代理审第 26–35 刀（26 语义收口/27 演示收官/28-30 优化计划三刀/31-33 数据三刀+交接/34 CI/35 评测尺）——设计符合性/评测数字底座/数据契约与多来源三路。其后 36 同义词接线（before 基线已立）。CONTEXT 推进句已回写。
