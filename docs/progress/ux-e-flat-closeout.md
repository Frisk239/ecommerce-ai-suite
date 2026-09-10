# UX-E1 closeout：CSS 去光泽（v3 扁平语言回归，平行工作流，不占刀号）

日期：2026-09-10。分支 `feat/ux-e-flat`（基于 main `44a8814`）。依据：`docs/ui-ux-plan.md` §四 UX-E Must 1 + §三 原则 + §九 第 8 条（发送钮只改色不改形）。**本刀只做 UX-E 的 CSS 一半**；去等宽卡/摘要条/文案/按钮层级/商品空表属 UX-E2（第 43 刀仪表的硬前置）。

## 规格（本刀）

把随 v3 重写一起落地并被记为「接受项」的装饰性光泽（`git log -S linear-gradient` 证实非后来漂移）扳回 v3 扁平：**画布/面板/统计卡/按钮/徽章/芯片一律实色，全站无 hover 位移**。发送钮保留 34px 圆形与流式原位替换（§二点八 交互冻结），只把品牌蓝改为近黑。

## 交付（仅 `apps/web/src/index.css` + 一行 TSX 清漏）

| 范围 | 改动 |
| --- | --- |
| 画布/面板 | `body` 去渐变与 `fixed` → 实色 `--color-canvas`；`.panel`/`.stat-card`/`.panel-hover`/`.panel-title` 白底实色 |
| 按钮 | `.btn-primary` 近黑实色 `#0F1115`、hover `#43454A`（§二点七 明列值）、**删 `translateY(-1px)`**；`.btn-secondary` 白底实色；`.btn-sm` 圆角 5→6 对齐按钮档；`.brand-mark` 同语言 |
| 徽章/芯片/分段/表头 | 全部纵向渐变 → 语义实色（色相不变，published 取旧渐变停靠点 `#e0f7e9`，不引入新色）；`.cite-chip`/`.tool-strip` 蓝/灰语义未动 |
| 内高光 | **全部 `inset 0 1px 0 rgba(255,255,255,…)` 顶高光删除**（UX-E 验收「面板无内高光」；浅色面上叠白于白本就无视觉作用，只留不一致） |
| hover 位移 | `.stat-card`/`.btn-primary`/`.cite-chip`/`.send-btn` 的 `translateY` 全删；`:active` 的 `scale()` 保留（UX-NOTES 细节纪律列为有意为之）；`ProductsPage.tsx:298` 漏网的 Tailwind `hover:-translate-y-0.5` 一并清除 |
| 发送钮 | **34px 圆形 + 流式原位替换不变**（冻结）；品牌蓝渐变 → 近黑 `#0F1115`、hover `#43454A`、投影去蓝 |
| 对话区 | `.chat-scroll` 渐变去化；顾客气泡回到 `--color-accent-soft`（§二点七 指定值）；`.skeleton` 扫光保留（加载态功能动效） |
| 降级 | `prefers-reduced-motion` 中已成死代码的 hover-transform 覆盖清理；头部注释口径修正（`:active` 缩放保留，非「全部静态」） |

## 验收（实测，非目测）

- 计算样式：`body/.panel/.stat-card/.badge/.seg-btn-active` `background-image:none`；`.btn-primary` 实色 `rgb(15,17,21)`、真实 hover 背景 `rgb(67,69,74)` 且 `transform:none`；`.send-btn` 34×34、`border-radius:9999px`、实色 `#0f1115`（禁用态读到的灰是 disabled 样式）。
- `grep linear-gradient` 仅剩 `.skeleton` 扫光；`grep "inset 0 1px 0 rgba(255,255,255"` = 0；剩余 `translateY` 仅居中定位/`:active`/关键帧。
- 1280 截图目检：面板白底墨线、统计卡实色、主钮近黑，无「AI 仪表盘」光泽。
- `npm run build` 绿；lint **7 warnings / 0 errors 与 main 基线一致**。

## 两轴评审与处置

两轴独立子代理均判**可合并（无 P0）**。已修：

- **P1**：`ProductsPage.tsx:298` 的 Tailwind `hover:-translate-y-0.5` 漏网（CSS 清了 JSX 没清，且与注释「全站无 hover 位移」自相矛盾）——已删。
- **Spec 主要缺口**：浅色面顶高光成片保留，与 UX-E 验收「面板无内高光」不符——已全部删除。
- 徽章 `#e6f7ee` 是新字面量——改回旧渐变停靠点 `#e0f7e9`；降级注释口径夸大——已修正。

记债（P2，归 UX-E2 或后续）：`.panel`/`.panel-hover` 仍用 `0 4px 12px` 级阴影做层级（§三「海拔=墨线」的张力，建议 E2 收）；画布 `#f5f6f7` 比侧栏 `#f9fafb` 深，与 §二点七「内容区 #FFFFFF」顺序相反（token 层面，未动）；圆角层级 modal 10 / composer 12 / 气泡 14 为刻意保留（不同表面层级）。

## 重要发现（不属于本刀，需 Owner 裁决）

工作队列去掉 16 条 CI 探针后，首屏仍是 183 条**原始灌入货**（英文 ABCD 会话 id、Wikidata/OFF 百科行）——「数据倾倒」观感仍在，只是换了一批行。`docs/ui-ux-plan.md` UX-A 只针对 CI 探针，未覆盖灌入数据本身的呈现。可选方向：①灌入行生成可读标题；②给演示库减量（只留主路径商品相关）；③按来源分组折叠。**建议进 UX-E2 或单独一刀。**

## 后续

- **UX-E2（总览摘要条/资产页去重复卡/文案压一句/运营投放次钮/商品空表）是第 43 刀仪表的硬前置**——E2 完成前不开 43。
