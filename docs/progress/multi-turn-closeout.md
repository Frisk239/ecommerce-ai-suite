# 工程第 29 刀 closeout：客服多轮记忆刀（feat/multi-turn）

日期：2026-09-09。上刀 intake：`docs/progress/lifecycle-exits-intake.md`（通过）。短对齐：`.scratch/multi-turn/spec.md`（优化计划 P2#4；goal ①「必须有：多轮」——0021 引擎层扩展无新 ADR）。基线=`origin/main`（c20b480，PR #35 合并后）。

## 交付（goal ①「必须有」最后一项兑现）

1. **`conversation_memory.py`**：`recent_turns`——会话最近 N=4 轮（refusal/handoff/tool 轮**整轮跳过含其问句**；返回前过 redact——0038 出口纪律）；`retrieval_query`——本问含代词（它|他|她|这个|那个|这款）且有上一问→检索词拼接，否则原样。
2. **`llm.stream_chat` 多轮化**：history 参数（customer→user/agent→assistant 映射，插 system 与本轮 user 间）；默认 None 与旧形状逐字节兼容（旧调用零改动）。
3. **`run_ask` 接线**：代词问句检索前拼接（compose/citations 语义不变）；生成分支传 history（取数在 LLM 前不持事务）；拒答/工具分支零额外查询。
4. **语义护栏**（spec 裁决）：记忆不改变检索与拒答判定——无证据仍拒答（哪怕上轮答过相关内容）；记忆只影响有证据时的生成语言。README 口述稿补指代示例。

## Owner 验收（浏览器实证）

同一会话：问「钛钢保温杯的净含量是多少」→引用回答；问「**那它的材质是什么**」（纯代词、单独检索必无命中）→**引用 A-0003+A-0009 回答 316 不锈钢**——检索词补全（上问主题并入）+生成层记忆（指代消解）双生效。

## 两轴评审（`docs/progress/multi-turn-review.md`）

净可合；P2×2（代词表窄于 spec 文本口径/「它」误命中无害）；计数环境差注明。

## 计数（摘自命令输出，junitxml 机械计数）

- ruff：All checks passed
- 无 DB：`tests=547 passed=403 failed/errored=0 skipped=144`
- 带 DB（5433）：`tests=547 passed=547 failed/errored=0 skipped=0`（基线只增；评审环境 collect 537 口径差已注明）

## 遗留

P2×2；跨会话/长期记忆/N 配置 Out；优化计划下一刀：**第 30 刀治理体验小刀**（缺口问句归一化+补文档问句提示+标题文件名兜底+商品页库存列）；**其后审计刀 6**（计数线）。CONTEXT 推进句已回写。
