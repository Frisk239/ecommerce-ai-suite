# 工程第 38 刀两轴评审：连接层协议证据（feat/mcp-evidence）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`c7e6453`+Owner 探针修复笔）。Spec：`.scratch/mcp-evidence/spec.md`（goal §6.2.5）。Owner 亲评（小刀：脚本+测试+README 三文件）。

**净，可合。** E2 探针设计亮点——直断「search 空」会被分词噪音击穿，改为**登记前后检索零差异+探针资产/标记词永不出现**（对分词鲁棒，语义等价「未发布不进索引」）；E1 集合相等防偷加工具；E3 反向断言钉死活状态不暴露（0021/0036 口径注释）；pytest 版 CI 可跑。Owner 复跑揪出一处并实修：smoke 默认探针资产 1→3（资产 1 修订流转停待人洗，get_asset 只读已发布会误报失败——脚本对演示库状态漂移的鲁棒性）。

P2 记债：E0 的 status 双值口径（ingested/pending_review）依赖机洗结果，严格说断言的是「未发布」而非「已接入」——语义可接受（两者都未发布）；探针资产累积在演示库（每次跑 --evidence 留一条待人洗资产，README 可注定期清理）。

## 计数（Owner 复跑）

- ruff：All checks passed；无 DB `645/487/0/158`；带 DB `645/645/0/0`（642→645 只增）
- 实跑 `--evidence`（修复后两次）：E0–E3 全 PASS，exit=0；证据摘要四行结构化输出
