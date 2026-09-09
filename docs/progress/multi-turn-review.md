# 工程第 29 刀两轴评审：客服多轮记忆刀（feat/multi-turn）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`307b6fd`）。Spec：`.scratch/multi-turn/spec.md`（优化计划 P2#4）。

**净，可合。** 跳过三态（refusal/handoff/tool 整轮含问句）双钉；history 出口过 redact（单测钉手机号）；取数在 LLM 前不持事务（拒答路径零额外查询）；无代词不拼接、拼接=字符串交 retrieve（bigram/阈值不变）；stream_chat 默认参数向后兼容（旧调用零改动，三替身仅加默认参语义等价）；无证据多轮仍拒答+不调模型钉测；顾客通道同享钉测；无迁移零禁区。

P2 记债：代词表窄于 spec 文本（六词窄表，「上面」未含——窄表合理，测试注释口径）；「它」误命中「其它/它们」（后果仅多拼上一问，可忽略）。计数口径：评审环境 collect=537（基线 523+14 纯增）；Owner 本机 junitxml=547/547 全绿（环境差以 Owner 为准记录，只增方向一致）。

## 计数（Owner 复跑，junitxml 机械摘取）

- ruff：All checks passed
- 无 DB：`tests=547 passed=403 failed/errored=0 skipped=144`
- 带 DB（5433）全量：`tests=547 passed=547 failed/errored=0 skipped=0`
- Owner 浏览器实证：同一会话先问「钛钢保温杯的净含量是多少」（引用回答）→再问「**那它的材质是什么**」（纯代词无主题词，单独检索必无命中）→**引用 A-0003 · v1 + A-0009 · v1 回答含 316 不锈钢**——检索词补全与生成层记忆双生效；README 口述稿补指代示例
