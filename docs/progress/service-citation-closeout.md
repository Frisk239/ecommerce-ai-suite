# Closeout · 工程第 3 刀：客服引用（feat/service-citation）

- 日期：2026-09-06
- 分支：`feat/service-citation`（`e865ecd` ADR 0023 → `0e2f70b` 实现 → 评审修复提交）
- 短对齐：`.scratch/service-citation/spec.md`（本地）

## 交付（用户路径全程）

操作者登录 → 客服页新会话 → 问「保温杯的净含量？」→ SSE 流式回答（thinking→逐字→完成）带引用芯片 `A-1 · v1` → 问待人洗独有内容 → 拒答+转人工（无引用）→ 「结束并回流登记」→ 对话资产 A-3（字节=转写，待人洗）→ 治理发布 → 再问同一问题 → 命中 `A-3 · v1`（中台一发布消费者当即可见，0017 实证）。

- **检索索引**（0017/ADR 0023）：`retrieval_chunks` 发布事务内切块写入（0004）；中文 bigram 词法打分（块/查询同口径过停用字）；只命中当前已发布版本（join 指针）；不调 embedding。
- **会话引擎**（0021 预览面）：SSE 状态机（UX-NOTES §四冻结：状态机保留、SSE 传输、35ms/mock 大脑不搬）；回答=证据组装模板（LLM 客户端留位不接通）；断连=后端完整落库（三处声明一致+SSE 断连测试钉死）。
- **拒答+转人工**（0018）：无证据→refusal+handoff 显性消息，固定文案，不编造。
- **引用**（0007）：citations 落库 JSONB 不漂移；引用芯片 `?v=N` 版本锚定（详情页横幅+高亮，回放语义）。
- **回流登记**（0013）：转写字节先落对象存储→kind=dialogue 资产→对话无必填（0019）→发布即进检索。
- **客服页**：历史会话条/新会话/流式 UI（typing 三点+「正在检索已发布资产…」+光标+停止原位替换+Esc+锁输入）/拒答徽章/回流确认与横幅/只读回看。

## 证据（Owner 亲跑，输出原文）

| 项 | 输出 |
| --- | --- |
| `uv run pytest` | `87 passed, 19 skipped, 2 warnings in 6.19s` |
| `SUITE_TEST_DATABASE_URL=… uv run pytest` | `106 passed, 11 warnings in 10.93s` |
| `uv run ruff check .` | `All checks passed!` |
| web build / oxlint | `✓ built in 335ms` / `2 warnings, 0 errors`（既有文件） |
| 浏览器点穿（IAB） | 问净含量→回答+`A-1 · v1`；问待人洗独有→拒答+转人工无引用；回流→A-3 待人洗只读；发布 A-3→再问→命中 `A-3 · v1`；引用芯片→`/platform/assets/1?v=1` 横幅+「引用锚定」高亮 |
| Alembic | 0002 up/down/up 可逆，九表齐 |

## 评审（两轴）与修复

硬违规 0、越刀 0。修复 7 项：块侧 bigram 过停用字（注释实现不符+分母噪音）；删死状态 CLOSED；引用芯片 `?v=N` 版本锚定；SSE 断连落库契约测试（+1）；登记路径提取 `services/registration.py`（消两路由同构复制+私有导入下沉）；ask() 双 commit 取舍注释；answer.py 正则口径统一。

Debt（评审记录不修）：工具失败不拿检索顶的契约测试（工具引入刀写实测试）；「工具失败」类纯 handoff 消息无产生路径；对话证据句选择策略（当前可能引用顾客原话行——LLM 刀重做组装）；retrieve() 全量拉取上限 10000（单店规模假设已注明）；多 active 会话并存无 UI 约束。

## Deviations

1. 后端子代理运行窗内出现未跟踪文件 `docs/research/data-flywheel.md`（14KB 数据飞轮调研，质量高但非本刀产物）——**未收进提交**，处置待人类定（收编/移动/删除）。
2. spec Must 3「工具失败不拿检索顶写进引擎契约测试」判为过度指定（本刀无工具，测试会空转）→ 转 debt 至工具刀。
3. IAB 无文件选择器（继承上刀）：铺演示数据经 API 脚本完成。

## 下一 Owner 注意

- 第 4 刀**待短对齐**，候选：**修订流+回滚**（ADR 0006/0016，发布页已留文案位）、**MCP 只读已发布**（0001/0020，检索索引已就绪可直接复用 0017）、顾客对话通道（0021 引擎已同源）。
- 演示主库现有数据：A-1 已发布规格、A-2 待人洗口径、A-3 已发布对话、两个会话（#1 已回流、#2 进行中）——可清库重来（compose down -v + rm data/objects/*）。
- gh 需 `HTTPS_PROXY=7890`；curl multipart 在 Git Bash 异常，用 Python urllib。
