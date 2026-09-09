# 工程第 35 刀两轴评审：RAG 评测尺（feat/rag-eval-ruler）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`5361ff3` 单提交）。Spec：`.scratch/rag-eval-ruler/spec.md` + goal §6.2.2 + 路 C §3。两轴合并轻评审（Owner 亲评——diff 为 scripts/eval 两脚本+synonyms 服务+18 离线测试+报告文档）。

**净，可合。** 两层评测口径清晰（CI 静态 13 条零改动/评测尺动态大集不入 CI）；生成与评测全纯标准库直调 retrieve+compose_answer（零 LLM 依赖，空 key 可跑）；固定种子双跑逐位一致（实现方两遍+Owner 复跑三方对照一致）；同义词表落检索侧 services/ 供 36 刀复用；golden.json 未动；报告数字为 stdout 机械粘贴。

P2 记债：judge 列当日网关不稳未入正式数字（演示跑通 43/79 后 LLMError，spec 允许跳过——网关稳定后补跑）；改写器句式变换模板较简单（36 刀 before/after 时可顺带丰富）。

## 计数（Owner 复跑，机械摘取）

- ruff（含 scripts）：All checks passed
- 无 DB：`tests=611 passed=463 failed/errored=0 skipped=148`
- 带 DB（5433）：`tests=611 passed=611 failed/errored=0 skipped=0`（基线 593 → 611 只增）
- 评测尺实跑（演示库 52 已发布资产、96 条四分布）：**recall@1 63.7% / @3 72.5% / 拒答组拒答率 100% / 正例误拒 2.5% / 混淆@1 60.0%**；同义组 @1 56.0% vs 正例组 70.0%（**-14pp = 第 36 刀同义词接线的 before 基线**）——报告 `docs/research/rag-eval-report.md`
- 内置浏览器（computer-use）实证：问评测集 pos-001「Cardiofitmd 的品牌是 1MD Nutrition 吗」→ 引用 **A-0245 · v1**（expect 一致）+正确回答（模板回退路径）
