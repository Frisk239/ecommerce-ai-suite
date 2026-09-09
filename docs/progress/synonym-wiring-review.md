# 工程第 36 刀两轴评审：检索同义词接线（feat/synonym-wiring）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`f48e4e3`+Owner 并集修正笔）。Spec：`.scratch/synonym-wiring/spec.md`（goal §6.2.2 before/after 纪律+审计刀 6 P1×5 吸收）。

**净，可合（含 Owner 关键裁决实修一笔）。** 实现方初版为**替换式归一**——Owner after 复跑裁决：净负（paraphrase @1 仅 +4pp 而 positive -5pp/confusion -13.3pp/overall -3.7pp，替换丢原词 bigram），按 goal「无提升不留」纪律**当场改并集扩展**（terms=原∪归一，token expansion，ES synonym 工业惯例同款；score 分子只增不减，数学上保证既有命中不丢）。并集版全分布非降：positive/confusion **零漂移实证**（70.0/60.0 与 before 逐位一致）、paraphrase @1 +4pp/@3 +8pp、overall @1 63.7→65.0。

审计 P1×5 全修：roadmap 旧口径/§35 措辞/README 幂等表（3 条 --load 幂等 vs 3 条重跑重复加粗）/goal 未完成行重校/ci-gate-closeout 口径。局限声明入报告（表内 16 组闭包自测+语料内表外探针为 0/461 的实证）。

## 计数（Owner 复跑，junitxml 机械摘取）

- ruff：All checks passed；无 DB `tests=617 passed=469 failed/errored=0 skipped=148`；带 DB `617/617/0/0`（611→617 只增）
- after 评测（并集版，Owner 复跑逐位一致）：positive 70.0/75.0（零漂移）、paraphrase **60.0/72.0**、confusion 60.0/80.0（零漂移）、refusal 100%、overall **65.0/75.0**
- 内置浏览器实证：「保温瓶的容量是多少」（双同义词改写）→ 引用 **A-0009 · v1** 命中——线上生效
