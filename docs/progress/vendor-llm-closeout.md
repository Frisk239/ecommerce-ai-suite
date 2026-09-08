# 工程第 7 刀 closeout：厂商生成（feat/audit-1 上 stack）

日期：2026-09-08。上游：审计刀 1 裁决（`docs/progress/audit-1-closeout.md`）+ ADR 0033（凭证已在 `.env`）。短对齐：`.scratch/vendor-llm/spec.md`（含验收后裁决补记）。

## 交付（一条厚路径）

客服页检索命中已发布证据 → **厂商模型流式生成**（openai 官方包 + base_url，20s 超时 0 重试）→ 完整落库后 SSE 流式（12 字/片 delta，双 thinking：检索→生成）→ citations 恒由服务端从检索命中定（0007，模型无引用决定权）。无证据拒答**不调模型**（0018，集成测试钉死 call count=0）。LLM 未配置/失败/空产出 → 降级 evidence 组装模板 + `complete.fallback=true` + 前端「模板回退」徽章（badge 中性灰，title 说明缘由）。

- `services/llm.py`：AsyncOpenAI 模块级懒建单例；`LLMError`/`LLMNotConfigured`/`LLMUnavailable`（通用文案不含密钥/端点）；`build_prompts`（中文系统提示 + 证据块带 `A-{id}·v{N}` 来源标注）。
- `routes/service.py` ask 步骤 2.5：`kind=="answer"` 才调模型；先收全再落库再流式（断连=完整落库契约保持，第 3 刀锁死口径）。
- 前端：`UiMessage.fallback` + 徽章；thinking 双事件兼容。
- 测试：`test_llm.py`（替身客户端，密钥纪律钉死）+ `test_vendor_llm_integration.py`（四路径：模型/失败降级/空 key 降级/无证据不调）。

## Owner 浏览器验收（真凭证栈，三条路径全过）

1. **真模型**：「保温杯的净含量是多少？」→ 「净含量为500ml。」+ 引用 `A-0003 · v1`（`/platform/assets/3?v=1` 版本锚定）+ 无徽章。
2. **拒答**：「会员积分怎么兑换？」→ 「拒答 · 无已发布证据」+「已转人工」+ 缺口芯片 `G-0001`，秒回（不调模型）。
3. **降级**：坏 `LLM_BASE_URL` 重启 → 同问题 → 模板回答 + 「模板回退」徽章 + 引用仍在。

## 验收揪出并修掉的两个缺陷（本刀最有价值的产出）

1. **opencode 网关硬性要求 `x-opencode-session` 头**（缺则 400 MissingSessionID）——stream_chat 全被包装成 LLMUnavailable 静默降级，**此前「真模型」回答实为模板**（浏览器两次回答逐字相同才暴露）。修复：每请求新 uuid（一问一对话，0023 无多轮记忆）+ 自定义 User-Agent（网关文档建议）。容器内实测 `STREAM-OK`，浏览器复验真模型路径。提交 `7cf5fb0`。
2. **web 容器旧镜像**：验收准备只重建了 api，5173 上 vite serve 的是镜像里的旧前端（无徽章代码），一度误判为前端 bug。教训：**浏览器验收必须核对 complete 事件 fallback 值**——短模型回答与模板肉眼难辨。

## 两轴评审与处置（`/code-review`，Standards/Spec 并行子代理）

- Standards：**零硬违规**；7 条 judgement call。实修：SSE 解析三处逐字重复 → `tests/sse_helpers.parse_sse_events`；`_MAX_EVIDENCE` 同名不同值改名。记债务：UiMessage 三处全量构造（顾客通道刀顺手收 `makeUiMessage` 工厂）、hits dict 第三消费者（Primitive Obsession）、`badge-ingested` 语义复用、路由懂 llm 契约（仓库厚路由风格可辩护）。
- Spec：五条 Must 全覆盖、无 scope 逃逸（UA/session 头属网关适配）。实修口径分叉：**prompt 证据上限 3→2 与 answer 引用上限同值**（进 prompt 的证据必须都可被引用，0007 回放口径）；ADR 0033 补实施澄清（「不进文档」= 凭证值，变量名/非密钥默认值可进 README，0022 回写先例）。「逐 token 透传」spec 原句与「先收全再流」实现的分叉在 spec 补记裁决（保断连契约，首字延迟=全文完成，边流边攒留顾客通道前再评）。提交 `688cadd`。

## 计数（摘自命令输出）

- 仓库根无 DB：`115 passed, 38 skipped in 6.32s`
- 带 `SUITE_TEST_DATABASE_URL` 全量：`153 passed in 18.34s`（上刀 152 + 新 session 头契约测试 1）
- `ruff check apps packages`：All checks passed
- web：`npm run build` ✓（357ms）；`npm run lint` 2 warnings 0 errors（预存 warning）

## 遗留（新增债务，接既有排期）

- TTFT：先收全再流（见上，顾客通道刀前再评边流边攒）
- async 路由内同步 DB 持连接至多 20s（0016 单操作者口径下可接受）
- 模型输出无服务端证据校验（引用是否真支撑回答——评测集范畴，0027）
- fallback/gap_id 重载不重现（运行时口径，0030 先例，契约非缺陷）

## 下一刀

顾客通道（ADR 0021/0033：会话令牌 + 每会话限流 + 每 IP 托底，同一引擎）；运营/切片/素材/考核继续排队。
