# 工程第 17 刀 closeout：素材中心（feat/material-center）

日期：2026-09-08。上刀 intake：`docs/progress/debt-1-intake.md`（通过）。短对齐：`.scratch/material-center/spec.md`；领域裁决：ADR 0038（任务状态机五态/生成质检登记/机洗打码步）；CONTEXT 词条「任务」状态句更新（单独成笔）。基线=`origin/main`（5bfc7ac，PR #21 合并后）。

## 交付（内容闭环第一段 + 打码）

1. **迁移 0009 `material_tasks`**：五态（queued/running/pending_qc/registered/failed——「可重试」读作属性，ADR 0038）/title/content/last_error/asset_id 回执锚；素材中心自有表非中台对象（检索/发布/血缘零触点）。
2. **`services/material.py`**：`run_generation_task`（同步就地：running 先 commit 再等 LLM ≤20s——P1#2 同款纪律；LLMNotConfigured/LLMError=failed「生成不可用」**无降级**；坏输出=failed；`qc_check` 四规则纯函数——非空/总长≤2000/标题非空/正文含商品名）；`approve_task`（pending_qc→register_asset(kind=material, source_kind=material_generated 服务端定值)→registered）；`reject_task`（人工打回）；`retry_task`（复位重跑）；非法转移 409。
3. **API `routes/material.py`**：六端点全操作者鉴权（建+同步执行/列表/详情/approve/reject/retry）。
4. **机洗打码步（audit-3 P1#4）**：`machine_wash.redact`（手机号前1后2掩码、邮箱 local≥3 掩码，环视防咬长数字串）三接入点——LLM QA 抽取 prompt 输入、parse 出的 q/a 值、文档正则字段值；**版本字节不动**（钉断言：版本 text 仍含裸号）；转写送厂商前已打码（钉断言 prompt 无裸号）。
5. **web 素材中心页**：侧栏入口、任务列表五态徽章（复用灰阶 token）、生成抽屉（商品下拉）、详情抽屉（文案 mono 预览+质检结果+通过/打回/重试按状态显隐+registered 跳治理台）；建任务/重试 30s 超时（LLM 20s+缓冲）。
6. **Owner 追加修复（验收揪出的潜伏偏离）**：必填闸门改 `kind=="document" and product`（发布端点+publishability 视图同口径）——第 2 刀以来闸门不分种类，词条锁死「素材没有规格必填」，素材刀第一次有挂商品的素材资产激活了它；集成断言按新语义钉死（文档 422 对照用例不动）。

## Owner 验收（浏览器点穿，全过）

登录→素材中心→生成「钛钢保温杯」卖点文案（真 LLM，M-0002 待抽检；第一轮揪出前端 15s 超时坑→修 30s 复测过）→详情抽屉文案预览+「抽检通过 · 登记为资产」→M-0002 已登记/A-0010→治理台 A-0010 待人洗（来源=素材生成）→**未确认字段直接发布成功**（闸门修复兑现，出现开修订/当前已发布）→客服问「316不锈钢饮水随行怎么样」→真模型回答引用 **`A-0010 · v1`**（素材进检索闭环）。「卖点」字面问句不引 A-0010 属词法打分自然结果（素材正文无该词），非缺陷。

## 两轴评审与处置（`docs/progress/material-center-review.md`）

零硬违规、六条 Must 全实证零渗越；实修两笔（AppShell 越位回滚、版本字节钉断言）；Owner 前置修复两笔（闸门 kind 条件、30s 超时）。记债务七条（storage 死参数/reject commit 层次/retry 覆盖行写/qc 截断口径/闸门条件两处复制/list N+1/updated_at 视图）。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=375 passed=279 failed/errored=0 skipped=96`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=375 passed=375 failed/errored=0 skipped=0`（基线 327 → 375 只增）
- web：`npm run build` 通过

## 遗留

- 评审债务七条（轻）；图片/视频素材、多模板、批量任务、队列 worker、定时生成 Out；M-0001（第一次超时请求实际完成）留在库里为待抽检——操作者可正常抽检/打回，无害。
- goal 能力块计数：4.5/7（素材已启动；切片/考核/运营仍缺）。

## 下一刀

刀计数：第 17 刀（审计后第 2 刀）。**第 18 刀=直播切片**（audit-3 排期：候选→拣选→登记 0014/0015+源录像词条）；审计刀 4 于第 20 刀后触发。CONTEXT 推进句已回写。
