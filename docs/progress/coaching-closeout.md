# 工程第 19 刀 closeout：销售考核（feat/coaching）

日期：2026-09-08。上刀 intake：`docs/progress/clip-picking-intake.md`（通过）。短对齐：`.scratch/coaching/spec.md`；领域裁决：ADR 0040（题库动态推导/单轮作答 LLM 三维打分/记录非中台对象/导出不建）。基线=第 18 刀合并内容（cb00db3；fetch 间歇 TLS 失败时以本地 feat/clip-picking 顶端为基，内容等价）。

## 交付（能力块 5/7→6/7）

1. **迁移 0011 `coach_records`**：题源锚 JSONB/题面/标准答案/受训者答案/三维分 JSONB/model_name 快照/last_error；考核模块自有非中台对象。
2. **`services/coaching.py`**：`derive_questions`（已发布 dialogue 的 confirmed qa_pairs 逐对成题；弃权/空→转写首问兜底 standard_answer=None；只认当前指针版）；`score_attempt`（作答行「未评分」先落库→LLM 等待不持事务→第二段事务 UPDATE 收口）；rubric=口径准确 40/证据贴合 30/服务语气 30，LLM 失败/坏 JSON→未评分可重评（`rescore_record`）。
3. **API `routes/coach.py`**：questions/attempts/records/rescore 四端点操作者鉴权；`GET /api/assets?kind=` 查询参数。
4. **web CoachPage**：题库列表（题源锚 `A-0009 · v1` 芯片链治理台）→作答抽屉（标准答案折叠参照/话术输入/提交评分）→三卡得分+评语+「扮演底座」徽章→记录倒序回放+未评分重评；侧栏入口。

## Owner 验收（浏览器点穿，全过）

题库从第 12 刀发布的 A-0009 QA 对动态推导出题「保温杯的净含量是多少？」→作答「您好，钛钢保温杯的净含量是 500ml…」→**真 LLM 评分 37/40 · 27/30 · 30/30**+底座 qwen3.8-flash 徽章→记录 #1 回放。空 key 无降级语义由集成测试钉（unscored 可重评）。

## 两轴评审与处置（`docs/progress/coaching-review.md`）

Standards 抓到 1 个硬违规（打分持事务跑 LLM，违反 debt-1 纪律）——实修（未评分先落库+双段事务）+2 条钉测；撤前端死签名、「上岗前」措辞删除；Spec 六条实质全命中零渗入。记债：题库全量重推导 O(N)、SourceChip 复用、三元组类型、兜底题证据维钉测。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=428 passed=323 failed/errored=0 skipped=105`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=428 passed=428 failed/errored=0 skipped=0`（基线 391 → 428 只增）
- web：`npm run build` 通过

## 遗留

- 评审债务四条（轻）；多轮追问/语音/上岗体系/批量/导出/多受训者 Out。
- goal 能力块：6/7（运营 Agent 仍缺）。

## 下一刀

刀计数：第 19 刀。**第 20 刀=血缘视图**（audit-3 排期末位：资产详情拼引用/写回/导出/考核使用，0026——此时下游⑤⑥③已齐，可一次拼全）；**其后触发审计刀 4**。CONTEXT 推进句已回写。
