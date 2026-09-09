# 工程第 33 刀两轴评审：数据契约（feat/data-contract）——交接补评

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`5decd87..9dab1f9`，单提交）。Spec：`.scratch/data-contract/spec.md`（post-hoc 补记——本刀原实现无书面 spec，为交接审查发现的流程缺陷之一）。评审为**独立子代理重跑**（原 3 行自评版作废，由本文替代）。

## Standards

- **P1×2（已实修）**：① closeout 声称的「Wikidata 补 P176/P2067 生成规格正文」半死——spec_doc/manufacturer/mass 仅产出无消费者，且对 P2067 裸值硬加 `" g"` 未解析单位（潜在编造净含量，踩 ADR 0009 红线）——实修：整段删除（连 SPARQL OPTIONAL 与 3 个固化测试），「商品规格文档接线」记 roadmap 待办；② OFF 手写 `{"净含量": required}` 绕开本刀自建的 category_schema 共享模板——实修：注释注明有意收窄（dump 无保质期、禁编造→弃权留给操作者），非绕过。
- **P2 记债**：`_net_content` 正则与机洗仍有一处内联重复；三脚本重复 try/except ImportError 模式；`_wands_schema` 位置；models/coaching/web 措辞改动属越界但零行为且 closeout 已声明。
- 达标项：category_schema 归 services/ 合理；无迁移且回填只补空；测试离线（评审子代理复跑 37 passed）；纯标准库延续 31-32 先例；不编造保质期有 fixture 断言；写回走既有 0010（publishing 零改动）；ODbL 署名正文+README 双落实。

## Spec（对照 closeout 声明，无原 spec）

已证：dump 清洗闸（fixture 5→2）；422→确认→发布→spec_values 写回→检索命中集成链；goal/README 口径改写。Chrome A-0227 与 595 passed 无法从 diff 复证（采纳 closeout 记录+评审算术自洽 583+12）。load_reviews 类目映射死代码确认删除。

**结论：可合**（两 P1 已实修，发布链无脏数据出口）。

## 计数（交接修复后 Owner 复跑）

- ruff（apps packages scripts）：All checks passed
- 无 DB：`593 passed / 0 failed / 148 skipped`（596 基线 −3 删除的死代码测试=593，含未跟踪 CI 测试）
- 带 DB（5433）：`593 passed / 0 failed / 0 skipped`
