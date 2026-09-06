# Closeout · 工程第 1 刀：脚手架（feat/scaffold）

- 日期：2026-09-06
- 分支：`feat/scaffold`（已推 origin；等待人类 review + 合并，Agent 不推默认分支）
- 提交：`5760bcb` 产品阶段基线入库 → `d4fcd9a` 脚手架 → `69dca55` 评审修复
- 短对齐 spec：`.scratch/scaffold/spec.md`（本地工件，未入库；Must/Out 见 `docs/slices.md`）

## 交付

- **单仓布局**：`apps/api`（suite_api，FastAPI + psycopg3 + pydantic-settings）、`apps/web`（Vite + React 19 + TS 单页健康壳，Tailwind v4 + oxlint）、`packages/platform`（suite_platform：ObjectStorage Protocol + LocalDirectoryStorage）；根 uv workspace + compose.yaml 三服务（pgvector/pg16 healthcheck、api、web）。
- **路径**：`docker compose up` → `localhost:5173` 健康卡双绿（真实调 `/api/health`）→ `uv run pytest` 绿。
- 无业务对象/表/ORM/MCP/登录；XAI 配置留位不建客户端。Out 十条全守住（Spec 评审子代理逐条核对）。

## 路径验收证据（Owner 亲跑，命令输出原文摘录）

| 项 | 输出摘录 |
| --- | --- |
| compose 栈 | 三容器 Up，db `Up (healthy)`（pgvector pg16） |
| `curl localhost:8000/health` | `HTTP/1.1 200` + `{"status":"ok","database":"connected"}` |
| `curl localhost:5173/api/health`（vite proxy 链路） | 同上 `200` + connected |
| 浏览器（IAB 1280×720） | 健康卡：`API 服务 可达 HTTP 200 · ok`、`数据库 已连通 connected`；视觉符合 DSH/StaffDesk 锚（白画布、墨线、品牌蓝仅语义）；VLM 评审：布局/配色/无渲染缺陷 |
| `uv run pytest`（评审修复后） | `13 passed, 2 warnings in 6.65s` |
| `uv run ruff check .` | `All checks passed!` |
| `apps/web npm run build` | `✓ built in 652ms`（198.85 kB / gzip 63.21 kB） |
| `apps/web npm run lint` | `Found 0 warnings and 0 errors.` |

## 评审（/code-review，两轴子代理）

- **Standards**：硬违规 0；5 个 judgement call。已修 2：settings 未加载 `.env`（README 的 `cp .env.example .env` 说明此前无效，补 `env_file=".env"`）、`.dockerignore` 补 `.env` 防御。
- **Spec**：Out 无越界；2 处存疑均为 spec 措辞歧义（settings 位于 api 而非 platform；壳只有顶栏无侧栏——单页加侧栏即空导航，违背 Out 精神），裁决不改。

## Deviations（过程偏差，如实记录）

1. 实现子代理自报 Dockerfile 两处运行时错误由 Owner 修复：uv 装到了不在 PATH 的 `/uv/bin/uv`（改 `pip install uv==0.12.1`，顺带摆脱 ghcr.io 不可达）；CMD 的 venv 路径错（uv workspace 把 venv 建在 `/app/.venv` 而非成员目录）。
2. 环境插曲：Docker Desktop 自动更新导致 daemon 间歇 500/EOF（用户在场确认）；宿主 5173 被子代理遗留 vite 进程占用（已清理）。
3. 评审修复后 pytest/ruff 复跑绿（见上表）。

## 债务（记录不修，进下刀施工单参考）

1. compose 的 web 服务跑 vite dev server（非生产形态）；生产构建 `/api` 无反代时 404——后续刀换多阶段构建时一并处理。
2. `db.py` 的 `timeout_seconds` 无调用方；`App.tsx` 两段状态三元组同形——第 2 刀搬原型交互时重构。
3. settings 位于 `apps/api` 而非 `packages/platform`（spec 措辞歧义，功能等价）；中台领域配置下沉 platform 时再动。
4. 壳无侧栏：第 2 刀随真实页面引入 AppShell（搬原型 v3 token）。
5. 环境注意：Docker Hub 拉取经本机代理偶发 EOF，重试或重启 Docker Desktop 有效。

## 下一 Owner 注意

- 合并方式由人类定：远端无 `main`，可将 `feat/scaffold` 设为 main 起点（仓库首条业务线）。
- 第 2 刀**待短对齐**，候选：A 治理发布写回 / B 客服引用（`docs/slices.md`）。不预埋空模块。
- 领域词条以 `CONTEXT.md` 为准；原型仅交互规格（`prototype/UX-NOTES.md` 第四节冻结口径）。
