# 工程第 15 刀两轴评审：评测集（feat/eval-set）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时 `57bf0c4`）。Spec：`.scratch/eval-set/spec.md` + ADR 0027。

## Standards

**零硬违规。** diff 仅三文件（golden.json / test_eval_set.py / README），产品代码零改动；词条口径合规（只锚检索引用/拒答/工具行为、无考核语义、无微调对比、README 重申不建表不做台）；测试惯例合规（module 级 api fixture、引擎级直调有先例、断言锚行为非实现细节）。

记债务（judgement，不阻断）：expect 三形状分派在 schema 校验与 runner 手写两遍；`_TOOL_NAMES` 手工镜像工具注册名；spectrum 自检靠 case id 含 "priority" 子串探测（改名静默失效）；golden.json 锚住摘要渲染格式（「·」分隔）；README「CI 位」措辞（仓库无 workflow 文件，实指随套跑）。

## Spec

**四条 Must 全达标，无缺失无越位无语义不符。** 覆盖谱 13 条逐一对上（引用×3 含 QA 对话命中、拒答×3 含两 policy edge、订单×4 含查无与优先边、库存×3）；policy edge 断言真钉语义（refuse 形状同时断言 kind/handoff/`tool is None`/citations 空）；runner 参数化读 JSON 直调 run_ask 不 mock 引擎；空 key 可复现；标题锚不锚 id；schema 自检指名 case id（缺字段/非法 expect/重复 id）；`tool_summary_contains` 与种子/模板实测核对一致。评审子代理实测：collect 331 ≥ 基线、eval-set 模块 15/15 rc=0（其环境 5432 被占致首跑误判挂起，换 5433 全绿——已知宿主端口坑，非产品问题）。

观察（非 gap）：spec 原文「既有 301 收集」为开刀时口径，上刀合并后实为 316（文档口径旧，不影响实现）。

**处置**：零代码修改，全 judgement 记债务。

## 计数（Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=316 passed=227 failed/errored=0 skipped=89`
- 评测集模块带 DB 单跑：`tests=15 passed=15 failed=0 skipped=0`
- 带 DB（5433）全量：`tests=316 passed=316 failed/errored=0 skipped=0`（基线 301 → 316 只增）
