# 第 75 刀 intake：来源权重——测量在先（roadmap 3.3 纪律）

上一刀：第 74 刀空 schema 治理激活（PR #107）。动机：审计刀 13 F5（净含量问句 top1
曾是探针回流资产）。候选方案：按来源加权（upload/material/clip ×1.25、
session_backflow ×0.9、review_import ×0.85——STALE_MULTIPLIER 同款后处理乘数）。

## 纪律

动检索路径必须评测 before/after；且金标 19 条期望 review_import、9 条期望
session_backflow——全局降权会翻正例。**测量在先**：对 80 条有期望的 case 算
top-1 变动，改善/变差/横移分类；再复测原观感问句。

## 结果（详见 rag-eval-report 第 75 刀节）

top-1 变动 1/80（横移，非改善）；原观感问题不修复（净含量 top3 不变、材质反而
变差）；金标结构约束权重空间。**结论：拒绝改动（无提升不留）**——病根是跨商品
混淆（confusion 组度量），属 rerank 级观察项；本实验数字留作将来 rerank 的对照基线。
