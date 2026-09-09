# 工程第 31 刀 closeout：多来源真实数据刀 I（feat/real-data-1）

日期：2026-09-09。上刀 intake：`docs/progress/gov-ux-intake.md`（通过，含调研刀 PR #38）。短对齐：`.scratch/real-data-1/spec.md`（优化计划第二阶段；`docs/research/real-data-sources.md` 组合拳前两源）。**数据工程刀：零产品代码改动**。基线=`origin/main`（7cd7926）。

## 交付（「多来源」设计点首次兑现——两源两通道）

1. **`scripts/realdata/fetch_wikidata_products.py`**：Wikidata SPARQL（六类 QID 实测校准：手机 Q22645/笔记本 Q3962/平板 Q155972/电视 Q8075/洗衣机 Q124441/图书 Q571；UA 头+Retry-After 退避应对 WDQS 限速 1 req/min）→ products.csv（CC0）→ `--load` 直连 DB 幂等灌库（复用 `suite_api.db.to_sqlalchemy_url` psycopg3 归一化——Owner 实跑揪出 psycopg2 缺失后修复）。
2. **`scripts/realdata/load_reviews.py`**：中文电商评论集（6.2 万条，GitHub 直下缓存）→ 固定种子抽样 → 转 (title,content) → **走既有 CSV 批量导入 API**（分批 200/次）→ `--publish N` 抽样发布。纯标准库（urllib/csv/zipfile）零新依赖。
3. 转换纯函数 16 例离线单测+双 fixture；`scripts/realdata/README.md`（来源/许可 CC0 与研究用途/全链命令/通道映射：商品=种子通道、评论=上传通道批量形态）；`.gitignore` out/。

## Owner 验收（真实数据全链演示库实证）

- Wikidata **91 个真实商品**入库（商品页 93 全带库存）；评论 **200 条导入+20 条发布**；
- **检索闭环**：问「平板会不会黑屏」→ 引用 **A-0028 · v1**（真实评论资产）命中；问未发布评论（「神奇校车」）→ 正确拒答留缺口——已发布过滤在真实数据上成立。

## 两轴评审（`docs/progress/real-data-1-review.md`）

净可合（零产品 diff/离线测试/许可口径/幂等）；Owner 前置修一笔（psycopg3 驱动归一化）。

## 计数（摘自命令输出，junitxml 机械计数）

- ruff（apps packages scripts）：All checks passed
- 无 DB：`tests=572 passed=426 failed/errored=0 skipped=146`
- 带 DB（5433）：`tests=572 passed=572 failed/errored=0 skipped=0`（基线 556 → 572 只增）

## 遗留

WDQS 限速使全量拉取约 8 分钟（README 已注明）；JDDC 需注册（数据刀 II 用 ABCD MIT 替代）；评论类目→商品挂接待后续。

## 下一刀

优化计划第二阶段：**第 32 刀多来源数据刀 II**（ABCD 会话回流模拟+WANDS 切片候选+相关性标注）→ 审计刀 6 → 评测实测刀。CONTEXT 推进句已回写。
