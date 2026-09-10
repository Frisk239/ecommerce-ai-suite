# UX-A1 closeout：工作台可扫读（前端，平行工作流，不占刀号）

日期：2026-09-10。分支 `feat/ux-a-workbench`（基于 main `6453b8d`）。规格：`docs/progress/ux-a-workbench-intake.md`；依据 `docs/ui-ux-plan.md` §四 UX-A + §九 S3/S4。目录回答模板（UX-A.5）拆到下一刀 `feat/ux-a-catalog-listing`（后端 + 评测纪律）。

## 交付

打开治理台第一眼不再是 CI 冒烟垃圾：**工作队列成为默认视角**，探针行（CI 冒烟 / 证据探针）默认不出现；列表可搜、商品可扫、空会话降噪。纯前端，后端零改动。

| 文件 | 改动 |
| --- | --- |
| `apps/web/src/workQueue.ts`（新建） | 谓词单一来源：`isWorkProbe`（`source_kind==='mcp_registered'` **且** 标题 `/^(mcp-smoke|evidence probe)/i`——组合条件，不误伤真实 MCP 登记）、`filterWorkAssets`、`parseAssetView`，以及三态口径 `isPendingWash` / `isPublished` / `isIngested` |
| `apps/web/src/pages/AssetsListPage.tsx` | 默认 tab=待人洗；点「全部」写显式 `?status=全部`（函数式 updater，`status`/`view` 互不抹掉）；`?view=work\|all` 范围开关（默认 work）；计数/列表/摘要卡全部经 `scoped` 单源分叉；标题/ID 客户端搜索；搜索态跨状态 + 「N 条匹配 · 搜索覆盖全部状态」提示 |
| `apps/web/src/pages/OverviewPage.tsx` | 计数改用同一套谓词（工作队列 + 三态），与列表同数字（§二点十 冻结原则） |
| `apps/web/src/pages/ProductsPage.tsx` | 默认排序「已定价 → 有写回规格 → 其余」（同档按 id 稳定）；「有写回规格」用 `spec_schema ∩ spec_values`（防类目切换残留旧键）；其余折进「其余商品 N」可展开区；删掉每卡重复脚注 |
| `apps/web/src/pages/ServicePage.tsx` | 0 消息会话徽章改中性「未开始」（不再显示「进行中」）、沉到有消息会话之后、默认选中最新一条有消息会话 |

## 相对方案的加固/调整（已记入规格）

1. **搜索跨状态**（规格外加固，验收 4 必需）：方案要求搜 `A-0029` 能命中，但该资产是**已发布**，默认「待人洗」tab 下会被状态过滤掉 → 有查询词时跳过状态 tab、在当前视图内全状态搜（实现与规格均记为裁决 7）。
2. **范围开关文案改「工作队列 / 全部登记」**（避免与状态 tab 的「全部」同词双义）。
3. **知识缺口 tab 下隐藏范围开关**（该 tab 数据走独立端点、不过工作队列，显示但点了无反应是误导）。

## 两轴评审处置

结论：**可合并，无 P0**。采纳并已修：

- **P1**：三态口径谓词此前复制在三处（列表 counts / 列表过滤 / 总览 counts）——正是本刀要防的债。已收敛为 `workQueue.isPendingWash/isPublished/isIngested` 单一来源，三处全部改调用。
- **P2**：折叠按钮补 hover（`panel-hover`）与 `cursor-pointer`；删掉在该上下文无规则生效的空转类 `row-caret`；`ServicePage` 排序注释改准（「组内按 id desc」，非「保持后端原有」）。

记债（P2，未改，均属仓库既有模式）：tablist 缺 `aria-controls`/`role=tabpanel`（全仓既有写法，本刀沿用）；三处稳定空数组写法不统一（`[] as const` / `EMPTY_*`）。

## 浏览器验收（playwright-cli，操作者 operator）

1. **裸进 `/platform/assets`** → 默认「待人洗」，`全部 245 / 待人洗 190 / 已发布 55 / 知识缺口 7`，列表 **190 行、零 `mcp-smoke`/`evidence probe`**。
2. **范围=全部登记** → URL `?view=all`，计数回到 `全部 261 / 待人洗 206`，探针行可见；再点状态 tab「全部」→ URL `?view=all&status=全部`（两参共存，互不抹掉）。
3. **计数口径**：只读 SQL 独立核对 `pending_review 无指针 = 206`、探针 `= 16`（全为待人洗），故工作队列待人洗 `190 = 206 − 16`；总览页显示「待人洗队列 190」，与列表一致。
4. **搜索**：`A-0029` → 1 条；`29` → 3 条（A-0229/A-0129/A-0029）；`保温杯` → 5 条（跨已发布/待人洗）——修前 `A-0029` 在默认 tab 下得 0 行，此为本刀发现的真问题。
5. **商品页**：首屏 23 张（3 已定价 + 20 有写回规格），`Trifolding phone` 等未定价不在首屏；点「其余商品 92」→ 115 张全量；重复脚注已消失。
6. **客服页**：列表前 8 条全为有消息会话，末尾 5 条为「未开始 · 0 条消息」；无「进行中 ·（还没有提问）」组合。
7. **知识缺口 tab**：只剩一个 tablist（资产状态筛选），范围开关隐藏。

## 数据卫生（S4 根因，本刀只治症状、未删库）

根因查明：写脏演示库的是 `scripts/mcp_smoke.py`（它对 `MCP_URL` 指向的 API 发登记请求，因此落库取决于该 API 的 `DATABASE_URL`；脚本自身不连库、不清库）。有人在演示栈上跑过 smoke 与 `--evidence`，留下 12×`mcp-smoke 保温杯` + 4×`evidence probe …`（id 1,2,246–259，全部 `pending_review`、无线上指针）。pytest 路径安全（走 `suite_test` 且自清理）。

**本刀不删库**（方案 §四 UX-A Out 明写；且 API 清不掉：`/discard` 仅限 `ingested`、`/revisions/discard` 对「从未发布的 v1」409；裸 SQL 会留孤儿对象与无审计的删除）。**留 Owner 裁决**：若要清，需事务内先删 `asset_versions` 再删 `assets`（16 行），并手动清对象存储对应键；或给 `mcp_smoke.py` 加「写演示库需显式开关」的守卫，防止再次污染。

## 门禁与诚实披露

- `npm run build` ✓；`npm run lint` 7 warnings / 0 errors——与 main 基线**逐数一致**（改动零新增告警）。
- 后端零改动（`git status` 仅 `apps/web/**` + 文档）；未删任何数据库行。
- **诚实披露**：① 前端无单测/e2e（仓库现状），验收靠 build/lint + 浏览器点穿；② **搜索态下 tab 计数与结果数不同步**（tab 仍显示队列计数，结果跨状态）——已用文案缓解，属已知取舍；③ 规格文档曾沿用过时数字（115 件「未定价」实为 115 总/3 已定价/112 未定价、NULL 标题实为 5 条），已在本刀订正。

## 后续

- UX-A2（目录回答模板改短，后端 + 大集零漂移与目录回归用例 before/after）为下一刀。
- UX-E 须在第 43 刀仪表前完成；本刀不改 roadmap 42–48 刀序。
