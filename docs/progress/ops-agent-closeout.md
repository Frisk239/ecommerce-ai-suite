# 工程第 22 刀 closeout：运营 Agent（feat/ops-agent）——能力 7/7

日期：2026-09-08。上刀 intake：`docs/progress/redact-exports-intake.md`（通过，随 align(22) 提交）。短对齐：`.scratch/ops-agent/spec.md`；领域裁决：ADR 0041（编排轨迹模块自有/投放=渠道动作非治理发布/gen_material 无降级/refs 冻结）。基线=`origin/main`（fa848c1，PR #27 合并后）。

## 交付（goal 七块能力最后一块）

1. **迁移 0012 `ops_runs`**：steps/output JSONB+delivered_at；非中台对象（检索/发布/MCP 零触点）。
2. **`services/ops.py`**：三步同步执行（read_product→gen_material=complete_chat 直接生成草稿**无降级**→compose=检索该商品已发布 material/video 资产取 refs 冻结当前指针版本，无引用时规格兜底+诚实披露「无已发布素材，正文由商品规格组装」）；running 前置 commit（LLM 等待不持事务）；失败断链后续停 pending；`retry_run` 从失败步续跑（done 不重跑）；`deliver_run`（投放=渠道动作，不改任何资产三态；已投放 409）；prompt 过 redact（0038 修订）。
3. **API `routes/ops.py`**：runs 建列表/重试/投放四端点操作者鉴权。
4. **MCP export 留痕**：成功导出写 audit_log（action="export"，「mcp」系统 operator 行 password_hash="!" 永不可登录）；血缘 writebacks action 集合不含 export（钉测）——血缘「导出」环自此有料可拼。
5. **web OpsPage**：三步轨迹（编号圆点/状态徽章/via mono/detail）+产出预览（引用芯片 `A-xxxx · vN`）+投放二次确认（明示渠道动作语义）+失败重试/重置按状态显隐；侧栏「运营 Agent」。

## Owner 验收（浏览器点穿）

轨迹与失败/续跑语义真环境实证：厂商网关对容器当时不可达（环境间歇故障，非产品缺陷——宿主代理可达、容器直连不通），编排跑出「步骤 1 完成（带规格 detail）→ 步骤 2 失败 · 可重试（生成不可用）→ 步骤 3 待执行」，重试后步骤 1 detail 逐字不变（续跑不重跑 done）——goal「编排可见、失败可重试」当场演示。成功面（三步 done/refs 冻结 v2 不漂旧 run/兜底披露/deliver 200→409）由 fake-LLM 集成测试钉死。

## 两轴评审与处置（`docs/progress/ops-agent-review.md`）

两轴净（P0=0/P1=0），评审子代理自跑带库 485 全绿；P2×3 记债（并发首插边角/audit 注释漂移/投放徽章显时间）。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=485 passed=366 failed/errored=0 skipped=119`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=485 passed=485 failed/errored=0 skipped=0`（基线 458 → 485 只增）
- web：`npm run build` 通过

## 遗留

- P2 三条（评审）；真渠道对接/批量/定时/效果回流 Out（部署语境）。
- **能力矩阵 7/7**：客服/中台/素材/切片/考核/连接层/运营全启动（连接层演示页、总览页观感项排第 23 刀）。

## 下一刀

刀计数：第 22 刀（审计刀 4 后第 2 刀）。**第 23 刀=总览页+连接层演示页**（纯前端演示面，补「七块都有入口」观感，audit-4 排期）；审计刀 5 于第 25 刀后触发。CONTEXT 推进句已回写。
