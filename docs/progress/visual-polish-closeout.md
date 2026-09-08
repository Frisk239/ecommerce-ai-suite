# 视觉收尾刀 closeout：工程 UI 对齐 DSH/StaffDesk（feat/visual-polish）

日期：2026-09-08。分支 `feat/visual-polish`（基于第 8 刀合并后 main）。本刀由另一会话开工（intake 顾客通道时顺带产出 `docs/research/demo-to-product-gaps.md`），Owner 会话接手完成审查、补齐与收尾。

## 交付两段

**第一段（4727e5d，原会话成果，Owner 替为提交）**：12 个前端文件、+609/-171——`index.css` 重写为 DSH token 体系（白画布/灰蓝侧栏/近黑主钮/rgba 墨线四档/语义徽章/骨架扫光/弹层动效），壳/页头/空态/横幅/气泡/各页打磨。build/lint 亲验后提交。

**第二段（6ed75f8，Owner 审查补齐）**：逐页截图审查（登录/资产/商品/客服/顾客/详情/404）+ 参照源码调研（StaffDesk styles.css 的 `.empty-guide`/`--sd-chat-width`/`button.primary`；DSH design-platform.css）后修四处：

| 问题 | 修法 | 依据 |
| --- | --- | --- |
| 顾客页空态吊在面板顶部，产品门面页大面积留白 | 面板内垂直居中 | StaffDesk `.empty-guide` 居中引导；截图 05→07 前后对比 |
| 已发布详情页「开修订」是唯一主动作却排次要样式 | 升近黑主钮 | 锚「主按钮近黑」；StaffDesk `button.primary` |
| 登录页径向品牌蓝光晕（装饰性用色） | 去光回白画布 | 锚「品牌蓝仅语义」「分层靠墨线」 |
| 资产行尾箭头常显 | hover/focus-within 才现 | StaffDesk「hover 才现的次级操作」；键盘可达性保留 |

## 审查确认到位免改

全局 `*:focus-visible` 蓝 ring（锚：蓝承担 focus）；`.btn` 过渡属性枚举（非 `transition: all`）；动效统一 cubic-bezier(0.4,0,0.2,1) 150-240ms + active scale(0.98)；404/Empty 虚线引导框；tabular-nums；行 hover 同色相叠加。

## 记录在案的接受项（翻案属翻新不属补齐）

- 气泡/徽章/面板的极淡竖向渐变（`#ffffff→#fcfdfd` 级）：与 DSH 平扁语言有微张力，可辨度低，截图评审不构成「AI 味」信号，保留。
- 侧栏仅两组三项（中台资产/商品、AI 客服）：随七块能力排刀进度长，非视觉债。
- 深色模式未做：UX-NOTES §三遗留，DSH 暗 token 已调研在案，工程版按 alias 变量切换即可（未排刀）。
- 顾客页无停止按钮（AbortController 即弃）：第 8 刀已知债务，非本刀。

## 评审口径说明

本刀为纯呈现层（CSS + 展示性 TSX，零行为变化），未走两轴子代理评审：审查本身即 Standards 轴（对照锚记忆 + 参照源码逐条），Spec 轴 = UX-NOTES §二点七 对照表（白画布/近黑主钮/语义蓝/墨线/高密度/细节纪律六行逐项核）。浏览器逐页截图 + computed style 探针（focus 环/行过渡/chevron opacity）为验收证据。

## 计数（摘自命令输出）

- `npm run build` ✓（338ms）；`npm run lint` 0 errors（28 files / 116 rules）
- 后端零改动：`git diff cac6281..HEAD -- apps/api packages` 为空
- 浏览器复验：顾客页居中 ✓、详情页主钮 ✓、chevron 默认 opacity 0 ✓、登录页无 radial 光 ✓

## 顺带入库（同分支）

- `docs/research/demo-to-product-gaps.md` + `reference/README.md` 四个新参考仓 + `docs/progress/customer-channel-intake.md`（原会话产物，随本刀收口入库；下一刀候选方向调研，未锁）。
