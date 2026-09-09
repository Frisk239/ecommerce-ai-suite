# 工程第 31 刀两轴评审：多来源真实数据刀 I（feat/real-data-1）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`2dc475a`+驱动修复）。Spec：`.scratch/real-data-1/spec.md`（优化计划第二阶段；数据工程刀零产品代码改动）。两轴合并轻评审（diff 仅 scripts/realdata/ 7 文件+测试）。

**净，可合。**（评审要点：零产品代码 diff 核实；脚本纯标准库零新依赖；测试离线不吃外网不连库；样本 fixture 手造；.gitignore out/；README 许可口径 CC0/研究用途+链接；幂等灌库 name+category 去重；Owner 驱动修复笔进 diff。）

## Owner 验收（真实数据全链，演示库实证）

- **Wikidata 商品**：`fetch --load` 实跑——SPARQL 六类（手机/笔记本/平板/电视/洗衣机/图书）**插入 91 个真实商品**（Vivo X300 Pro/Xiaomi 13T Pro/ThinkPad 等；WDQS 限速 1 req/min 带退避，全程约 8 分钟）；商品页 93 个（2 种子+91 真）全部带库存。
- **中文电商评论**：`load_reviews --n 200 --import --publish 20`——下载 6.2 万条评论集（3.9MB zip）→固定种子抽 200→**走既有 CSV 批量导入 API**（created 200/skipped 0）→发布 20 条（20/20）。
- **检索闭环**：客服问「平板会不会黑屏」→**引用 A-0028 · v1**（真实评论「平板评论·很一般的产品，常常黑屏」）回答命中；问未发布的「神奇校车」评论→正确拒答留缺口（G-0007）——已发布过滤语义在真实数据上依然成立。

## 计数（Owner 复跑，junitxml 机械摘取）

- ruff（apps packages scripts）：All checks passed
- 无 DB：`tests=572 passed=426 failed/errored=0 skipped=146`
- 带 DB（5433）：`tests=572 passed=572 failed/errored=0 skipped=0`（基线 556 → 572 只增）
- 演示库：products 93 / 上传文档资产 209 / 评论检索块 87
