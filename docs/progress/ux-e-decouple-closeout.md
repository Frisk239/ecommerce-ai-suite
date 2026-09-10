# UX-E2 closeout：视觉去卡与文案压句（平行工作流，不占刀号）

日期：2026-09-10。分支 `feat/ux-e-decouple`（基于 main `5b771ab`）。依据：`docs/ui-ux-plan.md` §四 UX-E Must 2–6 + §三 原则 + §六 分页面目标态。与 UX-E1（CSS 去光泽）合起来 **UX-E 全部 Must 达成，第 43 刀仪表前置就绪**。

## 交付（13 个前端文件，纯前端）

| Must | 落实 |
| --- | --- |
| 2 总览去等宽卡 | 五张 `stat-card` → **一条可点摘要条**（数字+标签横排，竖分隔线）；5 个跳转目标逐一未变；口径说明改 `title`/`aria-label` 承载。步进器 `flex-nowrap + overflow-x-auto`：1280 三行各一行不折，390 横向滚动不溢出 |
| 3 资产页去重复卡 | tab 下方三张与徽章同数字的卡删除；数字只留分段一处；下钻由 tab 自身承担（等价），口径说明移到 tab 的 `title` + `aria-label`（键盘/读屏可达） |
| 4 文案压句 | 9 个 `PageHeader desc` 各压到一句；`业务能力 ·` 前缀、ADR 编号（界面可见者）、`confirmed QA`、`rubric`、`.env.example`、侧栏「可整包替换」全部退出界面；404 去「工程控制台」口吻 |
| 5 按钮层级 | 运营「投放发布」→ `btn-secondary`（治理「发布」保持 `btn-primary`，全站近黑主钮语义唯一）；连接层政策条 `danger` 红 → 中性信息条 `bg-fill text-ink-2` |
| 6 商品页空表 | 规格表仅在 `hasSpec = hasWrittenSpec(product)`（`spec_schema ∩ spec_values`）时渲染，连分隔线一并去；无写回卡只剩名/类目/库存/价/编辑 |
| 附加 7 | `.panel` 双层阴影 → 单层近线级 `0 1px 2px rgba(16,24,40,.04)`；`.stat-card*`/`.stat-label/value/hint`/`@keyframes rise-in` 死代码删除（grep 零残留） |

## 验收（实测）

- 1280：总览 `.stat-card` 数 = 0、摘要条数字 190/0/55/7/63 与改前一致、三行步进器各一行不折；资产页 `.stat-card` = 0、desc 一句、190 行；商品页 23 张卡 21 张有规格表（2 张无写回的不渲染空表）。
- 390：四页 `document.scrollWidth === 390`（无横向溢出）；摘要条折两行、步进器横向滚动、末行省略号截断——可接受；表格仍是表（390 卡片化属 UX-G，未越界）。
- 静态核验：`OpsPage:261` `btn btn-secondary` + 未完成时提示「三步轨迹都完成后才能投放」；`AssetDetailPage:1161` 仍 `btn btn-primary`；`ConnectPage:184` `bg-fill text-ink-2`。
- `npm run build` 绿；lint **7 warnings / 0 errors 与 main 基线一致**；`grep linear-gradient` 仅剩 `.skeleton` 扫光。

## 两轴评审与处置

两轴独立子代理均判**可合并（无 P0/P1）**。已修（P2）：

- 页头压句误删真实能力：资产页补回「**可导出**」；商品页补回「**价格直写**，规格只在发布时写回」的对照（两者仍各一句）。
- 口径说明只挂 `title`，键盘/读屏不可达 → tab 增加 `aria-label`（含口径），`title` 保留。
- 运营「投放发布」禁用未写缺什么（违反 §三「禁用必须写缺什么」）→ 补提示。
- `flex-nowrap` 下 `gap-y-2` 成死类 → 删。
- 失败原因列空值由 `—` 改空白造成同表口径不一 → **回滚**为 `—`（与同表其他列一致；本次不做空值口径统一）。
- 残留实现词：`ConnectPage` 的「本机 .env」改「服务端配置」；404 文案去「工程控制台」。

记债（不阻塞 43）：侧栏/面包屑的 `业务能力 /` 分组名保留（属信息架构标签，非 `业务能力 ·` 标题前缀）；画布 `#f5f6f7` 比侧栏 `#f9fafb` 深，与 §二点七「内容区 #FFFFFF」顺序相反（token 层面）；`prototype/` 与 `docs/` 内仍含 ADR 号（非界面，不动）。

## 结论

- **UX-E（E1 + E2）全部 Must 达成**，§五「43 刀前 UX-E 必做完总览去卡」的硬要求已满足。
- 仍待裁的独立事项：**工作队列首屏仍是 183 条原始灌入货**（英文 ABCD 会话 id / 百科行），「数据倾倒」观感未根治——UX-A 只针对 CI 探针；建议单开一刀（灌入行可读标题 / 演示库减量 / 按来源分组折叠）。
