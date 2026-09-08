# 工程第 22 刀两轴评审：运营 Agent（feat/ops-agent）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（`5234902`+`483224a`）。Spec：`.scratch/ops-agent/spec.md` + ADR 0041（工作树版随 align 提交）。两轴合并评审，评审子代理自跑带库 485 全绿。

## Standards（净）

ops_runs 非中台对象（MCP/检索/治理零触点）；投放不改资产三态（词条「发布」_Avoid，页面明示）；LLM 步 running 前置 commit 纪律（残存 running 由 retry 复位有钉测）；prompt 过 redact（单测钉）；「mcp」系统 operator 行 `password_hash="!"` 非法 bcrypt 撞不到登录面（check_password 捕获 ValueError，401 钉测）；export 行不入 writebacks（action 集合钉测）。

P2 记债：ensure_mcp_operator_id 并发首插撞唯一约束可 500（单店低概率）；AuditLog action 注释仍写三值未含 export（注释漂移）；已投放徽章显时间非渠道名（后端无渠道字段，mock 语境可接受）。

## Spec（净）

Must1–6 逐条落地；Out 零越界；458→485 只增（22 单测+4 集成+1 MCP 留痕）。

## 计数（Owner 复跑，junitxml 机械摘取）

- ruff：All checks passed
- 无 DB：`tests=485 passed=366 failed/errored=0 skipped=119`
- 带 DB（5433）全量：`tests=485 passed=485 failed/errored=0 skipped=0`（基线 458 → 485 只增）
- web：`npm run build` 通过
- Owner 浏览器点穿：三步轨迹呈现（步骤 1 完成带规格 detail）；**失败面真环境实证**——厂商网关对容器当前不可达（环境间歇故障），gen_material 步「失败 · 可重试」+步骤 3 停「待执行」+重试后步骤 1 detail 逐字不变（续跑不重跑 done）——goal「编排可见、失败可重试」当场演示；成功面由 fake-LLM 集成测试钉死（三步 done/refs 冻结 v2 不漂/兜底披露/deliver 200→409）
