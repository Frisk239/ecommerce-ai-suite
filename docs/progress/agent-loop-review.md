# 工程第 37 刀两轴评审：客服真 loop（feat/agent-loop）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`e70161f`+`b7922a8`）。Spec：`.scratch/agent-loop/spec.md` + ADR 0043。两轴合并轻评审（Owner 亲评）。

**净，可合。** 快路径零回归铁证（订单/库存/多轮/vendor 既有测试文件零改动+新增哨兵测试证明快路径零 LLM 调用）；提议步走独立 `complete_tool_proposal` 非流式通道（不动 stream_chat 哨兵语义）；P1#2 纪律保持（提议等待前 commit）；授权三道（注册表查表/白名单键/参数校验）+越狱两形态钉测；实现方裁决合理——含单号混意图被快路径吞掉与零回归冲突，改用无单号问句演示模型提议（同测试内对照钉死两路径）。

P2 记债：`TOOL:` 文本约定对模型输出格式的依赖（弱模型可能不守格式→降级纯检索，可接受）；提议 prompt 未带库存工具商品名提示（模型提议 get_stock 无商品名时校验通过但执行空手——现有 query_stock 兜底回检索）；轨迹步数无显式 max_steps 落库字段（步数由结构上限隐含）。

## 计数（Owner 复跑，junitxml 机械摘取）

- ruff：All checks passed；无 DB `tests=642 passed=487 failed/errored=0 skipped=155`；带 DB `642/642/0/0`（617→642 只增）
- 内置浏览器实证（真 LLM）：混意图「帮我查下我的订单到哪了，顺便讲讲退货政策」→ 引用 **A-0006·v1 + A-0013·v3**，回答诚实分两部分：物流「当前证据未提供订单物流进度，无法帮你查询」（无单号不编造）+ 退货「支持15天无理由退货（升级版）」（引证据）——bounded Agent 的正确双意图行为
