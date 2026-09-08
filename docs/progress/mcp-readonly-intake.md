# Intake · 工程第 5 刀：MCP 只读已发布（feat/mcp-readonly）

- 日期：2026-09-08（Slice Owner 跨刀接手）
- Prev slug：`mcp-readonly`；分支 `feat/mcp-readonly`（`f87cbde` 实现 + `373ae1b` 评审修复/closeout）
- Merge 状态：**已合入 default** — `HEAD (373ae1b)` 是 `origin/main (e617152, PR #7)` 的祖先（`merge-base --is-ancestor` 通过）。本地 `main` 落后远端；下一刀从 `origin/main` 起 `feat/<slug>`。

## Evidence（Owner 复核，命令输出机械摘取）

| 项 | Closeout 声明 | Intake 复核 |
|---|---|---|
| `uv run pytest` | `106 passed, 27 skipped, 1 warning in 6.24s` | `106 passed, 27 skipped, 1 warning in 6.27s` + EXITCODE=0（本机复跑一致；27 skipped 为需真 Postgres 的集成） |
| `uv run ruff check .` | `All checks passed!` | `All checks passed!`（一致） |
| 集成 133 passed | 需 `SUITE_TEST_DATABASE_URL` 真库 | 未重跑，采信 closeout；单测零回归已复核 |
| smoke / 浏览器治理台 | 四工具全通 + 来源列=连接层登记 | 未重跑（需 compose 栈；采信 closeout + 代码/测试静态确认） |

## Spec vs claim（抽查 3 项）

1. **恰好四工具、无 publish** — PASS。`mcp_server.py` 只注册 `search_published` / `get_asset` / `register_asset` / `export_published`；`test_mcp_full_readonly_and_register_flow` 断言 tools/list 恰好该集合。
2. **Bearer fail-closed（缺/错/空一律 401，不读 cookie）** — PASS。`test_gate_rejects_missing_header` / `wrong_token` / `everything_when_token_config_empty` / `test_mcp_wrong_token_fails_initialize`；门只读 `Authorization`，操作者 `suite_session` cookie 结构性不经过 MCP mount。
3. **语义复用同一已发布权威** — PASS。search 走与客服同一 `retrieve()`；get 默认指针版 / 可取历史已发布 / 未发布统一口径拒绝；register 服务端定值 `source_kind=mcp_registered`、`kind=document`；export 含当前指针版正文全文。

## Safety

- `.env` / `data/` / `.scratch/` / `.playwright-cli/` 均 gitignore；`git ls-files` 无命中；`dev-mcp-bearer` 只在 `.env.example` / README / 冒烟用法说明；`mcp_smoke.py` 只从 env 读 token。
- Feature 提交无禁止产物入库。

## 债务（转交下一刀，不判返工）

承 closeout 5 项：stateless 无推送、真实域名需 `transport_security`、`read_version_text` 无大小上限、payload 小重复/不可达分支、MCP register 无 2MB 前置校验。

额外（塑形下一刀，非返工）：

- 修订不得把仍在服务的资产踢出 `status=published`（检索/MCP 在指针 join 之外还滤 `published`）；原型已按「有指针=已发布」处理修订。
- MCP register 不能带 `knowledge_gap_id`（0031 关缺口仍是控制台路径）。
- 客服 `answer.py` 仍是证据组装模板；`LLM_*` 仅 settings/.env 留位。
- 工程已交付 5 刀，Slice Owner 审计刀计数触发线已到（见短对齐）。

## Deviations（承接，不阻断）

FastMCP 为官方 SDK 内置类名（非第三方 fastmcp 库）；协议测走 ASGITransport；SDK 锁 1.x。均属实。

## Verdict

**通过** — 可进 step 3 短对齐第 6 刀。
