# Closeout · 工程第 6 刀：修订流 + 回滚（feat/revision-rollback）

- 日期：2026-09-08
- 分支：`feat/revision-rollback`（`a3436dc` 实现 → `a470a1e` 已接入确认闸门 → 评审小修）
- 基线：`origin/main` `e617152`（五刀已合并）
- 短对齐：`.scratch/revision-rollback/spec.md`（本地）
- 上一刀 intake：`docs/progress/mcp-readonly-intake.md`（通过）

## 交付（操作者路径全程）

操作者打开已发布规格 → **开修订**（复制当前已发布字节到新对象键，`version_no=max+1`，确认字段标 inherited）→ **status 仍为 published、指针仍指 v1**（客服/MCP 继续命中线上版）→ 列表已发布 tab 显示 `待人洗 · 修订中` + `线上 vN`，待人洗 tab 不见该行 → 人洗可改未发布版（改动丢掉 inherited）→ **发布 v2**（指针前移、切块入索引、写回商品、audit publish）→ 版本历史对旧已发布版 **回滚到此版**（单独移指针、写回该版字段、audit rollback；有未发布修订时 409）。

- 迁移 0004：`UNIQUE (asset_id) WHERE published_at IS NULL`。无第四态。
- 0031：缺口所挂商品已有已发布 document 时，去补对该资产 `openRevision` 并挂 `knowledge_gap_id`；否则仍登记抽屉。
- MCP 无新工具；`get_asset` 默认跟指针，未发布修订仍统一口径拒绝。

## 证据（Owner 亲跑，输出原文）

| 项 | 输出 |
| --- | --- |
| `uv run pytest` | `107 passed, 33 skipped, 1 warning in 6.44s` |
| `uv run ruff check .` | `All checks passed!` |
| 集成（SUITE_TEST_DATABASE_URL） | **未跑**：本机 `com.docker.service` 停且无管理员权限拉起；5432/5433/5434 无监听。账本测试已写在 `test_governance_integration.py` / 客服与 MCP 指针测已改走真开修订。CI 或本机 Docker 起来后须补跑。 |
| web lint | `Found 2 warnings and 0 errors.` `Finished in 70ms on 26 files`（既有 warnings，非本刀引入） |
| web build | 实现子代理：`✓ built in 11.72s`（本刀未再改 web 后未复跑 build） |
| 浏览器点穿 | **未跑**（无 compose 栈）。债务。 |

## 评审（两轴）与修复

硬违规 0、越刀 0。修复：已接入版本确认仍 409；开修订 `knowledge_gap_id` 只走 JSON body；审计/缺口注释对齐 rollback 与修订关缺口。其余判断项见 `docs/progress/revision-rollback-review.md`。

## Deviations

1. 原型 `OPEN_REVISION` 把 `asset.state` 打成待人洗；工程 **不照抄**（检索/MCP 滤 `status==published`）。列表口径跟原型：已发布=指针非空。
2. 原型无回滚按钮；工程按 UX-NOTES「回滚单独移指针」补了版本历史动作。
3. 本机 Docker 起不来，集成与浏览器验收记债务，不假装绿。

## 债务

1. 集成 33 skipped 与浏览器点穿须在 Docker/CI 补跑。
2. 拒答缺口 `product_id` 恒空 → 去补多数仍走新登记；0031 要带商品的缺口才自动开修订。
3. 去补取该商品「最新已发布 document」，不一定是规格那一份。
4. 二次开修订 409 前已写对象键，并发可能留孤儿字节（同登记模式）。
5. 无放弃修订；有未发布修订时回滚 409。
6. 开修订不提供换正文上传（v2 正文=v1 拷贝，只改字段）。
7. Slice Owner 审计刀计数线在第 5 刀已到，本刀先做产品；下一刀候选含审计刀。

## 下一 Owner 注意

- 更后面：厂商生成（0033，`.env` 已留位）、顾客通道（0021/0033）、**审计刀**（不改产品代码）。
- 合入前：起 Docker 后 `SUITE_TEST_DATABASE_URL=postgresql://suite:suite@localhost:<PG_PORT>/suite_test uv run pytest`；浏览器走开修订→发布 v2→客服引用 v2→回滚→引用 v1。
- 环境：本机 Docker Desktop 服务停着；5432 历史上常被 suanming 占用，用 `PG_PORT`。
