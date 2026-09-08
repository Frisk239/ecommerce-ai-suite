# 工程第 19 刀两轴评审：销售考核（feat/coaching）

日期：2026-09-08。Fixed point：`feat/clip-picking...HEAD`（评审时 `61a0085`/`b9be799`，处置 `122dbf2` 在后）。Spec：`.scratch/coaching/spec.md` + ADR 0040。

## Standards

**抓到 1 个硬违规（已实修）**：`score_attempt`/`rescore_record` 在持有 session 事务状态下同步跑 LLM ≤20s（idle-in-transaction）——违反 debt-1 锁定纪律。实修：作答行「未评分」态先 INSERT commit、rescore 读行取完 prompt 输入后 commit，LLM 等待后第二段事务 UPDATE 收口；补 2 条钉测（complete_chat 时刻 `in_transaction()` 恒 False；INSERT 先落库）。

口径确认通过：记录非中台对象（检索/发布/审计/MCP 零触点）；题库只抽已发布+confirmed；「不落分」=score NULL 未评分可重评（与 ADR 自洽）；与评测集边界清晰（打的是人）；standard_answer 下发=spec 裁决实现一致。

实修另两笔：撤前端 `listAssets(kind?)` 死签名（后端 ?kind= 保留有测试）；CoachPage「上岗前」措辞删除（词条 Avoid「上岗系统」避嫌）。

记债务（judgement）：作答每次全量重推导题库 O(N)（含兜底 IO）；题源 Link 手写复制 SourceChip；{题面,标准答案,答案}三元组穿多层可捆类型；question_key JSONB 只当序列化用。

## Spec

**六条 Must 实质全命中，Out 零渗入。** derive/打分三态/四端点鉴权/三卡+底座徽章/记录回放+重评/kind 过滤全落地有测试；`last_error` 列为「未评分可重评」语义的合理加法（docstring 记载）；观察项：兜底题「证据维」语义（ADR「只看另两维」vs 实现「不因缺标准答案扣证据分」）可辩护但无钉测——记候看。

## 计数（处置后 Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=428 passed=323 failed/errored=0 skipped=105`
- 带 DB（5433）全量：`tests=428 passed=428 failed/errored=0 skipped=0`（基线 391 → 428 只增）
- web：`npm run build` 通过
- Owner 浏览器点穿：题库从 A-0009 动态推导出题→作答→**真 LLM 评分 37/40 · 27/30 · 30/30**+底座徽章 qwen3.8-flash→记录回放
