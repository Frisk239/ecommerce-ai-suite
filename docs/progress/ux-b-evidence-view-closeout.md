# UX-B closeout：引用只读证据视图（平行工作流，不占刀号）

日期：2026-09-10。分支 `feat/ux-b-evidence-view`（基于 main `a5f97ab`）。规格：`docs/progress/ux-b-evidence-view-intake.md`；依据 `docs/ui-ux-plan.md` §四 UX-B + §九 S1。

## 交付

**产品主张「引用可核验」从口号变成可点穿的事实**：引用芯片 `?v=N` 命中已发布版本时进入**只读证据视图**——固定横幅 + 该版正文（含视频转写/素材文案）+ 字段锁同一快照，全部写动作隐藏。后端零改动（复用既有 `GET /assets/{id}/versions/{n}/text`）。

改动 4 个前端文件 + 1 条冻结口径登记：

| 文件 | 改动 |
| --- | --- |
| `apps/web/src/pages/AssetDetailPage.tsx` | 只读谓词 `readOnlyEvidence`（anchor 已发布即只读）；`viewedVersion = readOnlyEvidence ? anchorVersion : activeVersion` 让字段/QA 也锁到 anchor；`TranscriptPanel` 抽为通用 `VersionTextPanel({assetId,versionNo,title,note,errorLabel,emptyText})`；只读时隐藏字段编辑/QA/发布/上传/放弃修订/开修订/重新验证/回滚；页头与侧栏换只读文案；抑制旧的「引用回放锚定」蓝字避免双横幅 |
| `apps/web/src/components/Banner.tsx` | 新增 `InfoBanner`（品牌蓝状态横幅，`role="note"`，带 `action` 槽位） |
| `apps/web/src/components/MessageBubble.tsx` | 顾客侧 `CitationChipPlain` 换中性变体 `cite-chip-readonly`（仍是 `<span>`，不可点） |
| `apps/web/src/index.css` | `.cite-chip` / `.cite-chip-readonly` 抽共享基类（尺寸/等宽单一事实源），只读变体补同款 inset 白高光 |
| `prototype/UX-NOTES.md` | §二点七 蓝色语义枚举补记「状态横幅（信息语义，低 alpha 边/底，无渐变无位移）」——避免冻结口径分叉 |

## 与方案原稿的偏离（已留档，属裁决）

方案 §四 UX-B Must.4 字面写「v 与工作版本相同 → 保持治理编辑页」。实现改为**anchor 只要已发布就只读**（`AssetDetailPage.tsx` 内 `readOnlyEvidence`）。

理由：引用芯片恒指向已发布版（检索只回已发布），而「引用指向**当前**已发布版」恰是最常见情形——本会话实测的 `A-0029 · v1`（asset 29 只有一个已发布版本）就是这种。若按字面排除，点引用仍看不到正文，方案自己的验收「客服点芯片 → 看见该版原文」直接不成立。因此把 Must.4 的「工作版本」读作待人洗/修订**草稿**：`?v=` 指向未发布草稿（手改 URL 才会发生）时仍是可编辑页。两轴评审均判定此为「规格已裁决的合理偏离」，不构成阻塞。

## 两轴评审处置

结论：**可合并，无 P0/P1**（Standards 6 条 P2；Spec 逐条满足、无缺口无越界）。采纳并已修：

1. `.cite-chip-readonly` 与 `.cite-chip` 重复 9 条声明 → 抽共享基类；并补上本仓 20px 级芯片统一的内高光（此前遗漏）。
2. `InfoBanner` 的 `role="status"`（live region，用于动态更新）用在静态页面状态上不准 → 改 `role="note"`。
3. `InfoBanner` JSDoc「…等」会诱使品牌蓝扩张到一切中性提示 → 收窄为「状态横幅（信息语义）」；并把该表面登记进 `UX-NOTES §二点七` 蓝的枚举。
4. 只读快照下仍显示「（发布必填：…）」而本页无发布动作 → 只读时隐藏。
5. 删掉复述 JSX 的行内注释。

记债（P2，未改）：`evidenceVersionNo` 与 `viewedVersion` 双变量编码同一条件、横幅/侧栏直接插值 `anchorVersionNo` 与面板走 `evidenceVersionNo` 两条取值路径；`VersionTextPanel` 的 `errorLabel` 名不符实（实为「主体名词」，兼用于 loading）；`InfoBanner` 命名偏泛。

## 浏览器验收（playwright-cli，localhost:5173，操作者 operator）

逐条对照规格验收表，全部属实：

1. **客服点芯片**：`/service` 会话 #59 的 `A-0029 · v1`（可点蓝链）→ 落在 `/platform/assets/29?v=1`，横幅「只读证据视图：引用指向 v1 的不可变快照。要改内容，请在待人洗版本上开修订。」+ 正文（真实评论原文）+ 主栏**唯一按钮是「血缘」**（零写动作）。
2. **无 `?v=` 仍是治理页**：`/platform/assets/29` → 有「开修订（新待人洗版本）」主钮、无证据横幅。
3. **历史已发布版**：`/platform/assets/13?v=1`（当前指针 v3）→ 正文「退货政策：7天无理由退货」（v1 口径，v3 才是「15天」）+ 「结构化字段 · v1」+「只读证据视图 · 快照字段只读」——**正文与字段同属 v1 快照**，无「正文 v1、字段 v3」矛盾。
4. **非 document 种类**：`/platform/assets/11?v=1`（video/clip_pick）→ 显示转写「[00:02:14-00:02:52] 现场实测保温…」；`/platform/assets/10?v=1`（material）→ 显示素材文案。
5. **顾客通道芯片**：`/customer` 提问后，引用芯片为 `SPAN`、class `cite-chip-readonly`、底色 rgb(235,238,242) 中性灰、cursor default、不在 `<a>` 内；「没有帮助」按钮未改。
6. **操作者侧芯片未回归**：`.cite-chip` 仍是 `<a>`、rgb(237,243,254) 品牌蓝底、cursor pointer。

## 门禁与真实现自我检查

- `npm run build` ✓（tsc + vite）；`npm run lint` 7 warnings / 0 errors —— 与 main 基线**逐数一致**（改动零新增告警）。
- 后端零改动（`git status` 仅 `apps/web/**` + 文档），后端测试无回归面。
- **诚实披露**：① 非 UTF-8 版本取正文的**失败态**未用真实数据触发（演示库无此版本），该分支与既有的人洗 `TranscriptPanel` 同源、由同一 `requestText` + `VersionTextError`(409) 路径保证，属代码级验证而非实测；② 前端无单测/e2e（仓库现状），本刀验收靠 build/lint + 浏览器点穿，未新增前端测试基建。

## 后续

- UX-A（工作台可扫读，含数据卫生前置）为下一包；UX-E 须在第 43 刀仪表前完成。
- 本刀不改 roadmap 42–48 刀序（平行工作流）。
