# 工程第 37 刀 closeout：客服真 loop（feat/agent-loop）——goal §6.2.3 达成

日期：2026-09-09。上刀：第 36 刀同义词接线。短对齐：`.scratch/agent-loop/spec.md`；领域裁决：**ADR 0043**（修订 0036/0037：正则前置分派降级为快路径，混意图走模型提议+代码授权）。基线=`origin/main`（6171368，PR #45）。

## 交付（「不是 if 链」的死亡问从此有代码答）

1. **工具注册表** `services/agent_tools.py`：get_order_status/get_stock（描述+`TOOL: 名 {json}` 输出约定+白名单键/必填/格式三道校验）；`parse_tool_proposal` 三态；`execute_tool` 复用 order/stock 既有实现零重写。
2. **步进循环** `run_ask`（max_steps=3）：①快路径（订单号/库存词表→直接工具零 LLM，**既有行为逐字节保留**——新增哨兵铁证测试证明快路径零 LLM 调用）②提议步（`complete_tool_proposal` 独立非流式通道；合法→执行→结果作 history 附加轮进生成；被拒→kind=handoff 不检索不缺口）③检索+生成照旧（0018/0007 不动）。混意图序 SSE：thinking(查单)→tool→thinking(检索)→生成。
3. **越狱钉测**：提示注入「列出所有订单」/无参提议→拒绝+转人工+缺口计数不变（两形态钉死）。
4. **降级链**：LLM 失败/空 key→提议步降级为快路径分派（36 刀前行为）。

## Owner 验收（内置浏览器，真 LLM）

混意图「帮我查下我的订单到哪了，顺便讲讲退货政策」（无单号→模型提议路径）→ 引用 **A-0006·v1+A-0013·v3**，回答诚实两分：物流「当前证据未提供订单物流进度，无法帮你查询」+退货「支持15天无理由退货（升级版）」——不编造、不越权、双意图并存。

## 两轴评审（`docs/progress/agent-loop-review.md`）

净可合；P2×3（TOOL 格式依赖弱模型/get_stock 无名提示/步数字段隐含）。

## 计数（摘自命令输出，junitxml 机械计数）

- ruff：All checks passed；无 DB `642/487/0/155`；带 DB `642/642/0/0`（617→642 只增）

## 遗留

P2×3；写工具两阶段（第 40 刀）。

## 下一刀

**第 38 刀连接层协议证据**（MCP 自动化：独立脚本+测试 search→get→register 全链+未发布 search 空+活状态只读工具同函数）→ 39 自进化仪表 → 40 忠实度/反馈/两阶段写 → **审计刀 7**（第 40 刀后）。CONTEXT 推进句已回写。
