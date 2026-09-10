# UX-B 刀规格：引用只读证据视图（平行工作流，不占刀号）

日期：2026-09-10。分支 `feat/ux-b-evidence-view`（基于 main `a5f97ab`）。依据：`docs/ui-ux-plan.md` §四 UX-B（+ §九 S1：本项是全方案唯一 grill 级、且后端零改动）。

## 为什么要做

产品主张「引用可核验」目前不成立：客服消息的引用芯片带 `?v=N`，点进去落到**可编辑工作版本**，主栏看不到该版正文（`AssetDetailPage.tsx:642-648` 只把 anchor 用于高亮版本列表）。grill 一戳就穿。后端 `GET /assets/{id}/versions/{n}/text` 已存在且对 document 可取字节（实测 asset 13/29），故本刀**纯前端**。

## 规格（Must）

1. **只读判定**：`?v=N` 命中一个**已发布**版本即进入只读证据模式。
   - 之所以不排除「anchor === 当前 activeVersion」：引用芯片恒指向已发布版（检索只回已发布），而「引用指向当前已发布版」恰是最常见情形（例：会话 #59 的 `A-0029 · v1`，asset 29 只有一个已发布版本）。若排除它，点引用仍看不到正文，验收不成立。方案 item 4 的「工作版本」按待人洗/修订**草稿**理解。
   - 仍保持「无 `?v=` → 今天的治理页」；anchor 指向未发布草稿（手改 URL）时才留在可编辑页。
   - 只读时主栏字段也锁到 anchor 那一版（`viewedVersion`），避免「正文 v1、字段 v3」自相矛盾。
2. **固定横幅**：文案「只读证据视图：引用指向 vN 的不可变快照。要改内容，请在待人洗版本上开修订。」+ 次级链接「去工作版本」→ `/platform/assets/{id}`（不带 `?v=`）。
3. **主栏展示该版正文**：复用 `api.getVersionText(assetId, N)`；必须有 loading / error / ok 三态（失败态显示后端 detail，如非 UTF-8 的 409 文案），不得白屏或假装空。
4. **只读时隐藏全部写动作**：字段编辑（`FieldRow`）、QA 编辑（`QaPairsEditor`）、发布主钮、上传新正文、放弃修订、「开修订」主钮。页头 desc 与字段说明换只读文案。
5. **顾客通道芯片不可点、不像链接**：`MessageBubble.CitationChipPlain` 换中性变体（无品牌蓝底、无 hover 上浮、cursor 默认），保留 `A-xxxx · vN` 文本与「没有帮助」按钮（后者本就是 `btn-secondary`，不动）。操作者侧 `CitationChip`（Service/Products/Ops/Connect）保持可点蓝链不变。

## Out

- 后端零改动；不新增证据对象；不改引用写入；不做顾客端引用跳转；不碰发送钮/视觉 token（属 UX-E）。

## 验收

1. 客服 #—点引用芯片 → 只读横幅在、该版正文在、字段不可改、无「开修订」。
2. 直接开 `/platform/assets/:id` → 仍是编辑页。
3. `/platform/assets/:id?v=<未发布草稿版本号>` → 仍是编辑页（手改 URL 才会发生）。
4. 引用指向当前已发布版（最常见，如 `A-0029 · v1`）→ 也进只读并显示该版正文。
5. 顾客通道芯片中性、点不动；操作者侧芯片仍可点。
6. 非文本版本取正文失败 → 显示失败态。
7. `npm run build` + `npm run lint` 绿；后端测试不回归。

## 已知坑

- `anchored` 现在只验「版本存在」，必须补「已发布」判定，否则日常编辑页被误判只读。
- `TranscriptPanel` 硬编码 dialogue 文案（「对话转写」等），复用到 document 会文不对题——抽通用 `VersionTextPanel` 或加 props。
- `cite-chip` 类被多个页面共用，只能改顾客侧的 plain 变体，别动通用类。
