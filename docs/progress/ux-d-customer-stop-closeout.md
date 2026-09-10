# UX-D closeout：顾客停答与流式生命周期（平行工作流，不占刀号）

日期：2026-09-10。分支 `feat/ux-d-customer-stop`（基于 main `fd57444`）。依据：`docs/ui-ux-plan.md` §四 UX-D + `prototype/UX-NOTES.md` §二点八（停止=Esc 或圆形钮原位替换；停止保留部分文本并标记）。**本刀排在主线第 42 刀之前**——两刀同改 `CustomerPage.tsx`（42 在 handoff 气泡下挂表单），顺序错了会打架（方案 §五 已提示）。

## 规格

顾客通道与操作者预览复用同一状态机：流式中发送钮**原位变停止钮**、Esc 停止、停后输入解锁；`useAskStream` 卸载即断订阅；停止的标记口径与 `/service` 同一文案族。

## 交付（2 个前端文件）

| 文件 | 改动 |
| --- | --- |
| `apps/web/src/hooks/useAskStream.ts` | ① 抽出 `pruneOrStopAgent()`（顾客通道收尾的唯一出处）：收到过事件则标 `stopped`，空占位剪掉——空回答没有留档，「已留档」对它是不实陈述；catch（失败路径）与 `finally`（停止路径，`abort` 是静默返回、不进 catch，见 `client.ts:143`）共用它。此前顾客页停止后气泡会永远停在 streaming。② 新增卸载清理 `useEffect(() => () => abortRef.current?.abort(), [])`——**共享 hook，故操作者预览同样获得「离开即断订阅」** |
| `apps/web/src/pages/CustomerPage.tsx` | 取用 hook 的 `stop`；composer 发送钮原位替换为 `.send-btn.send-stop`（停止图标 + `aria-label="停止输出"`）；流式期 `Escape` 停止（与 `ServicePage:160-167` 同实现）；占位文案补「（Esc 停止）」 |

## 验收（实测）

- 流式中：发送钮类名变 `send-btn send-stop`、`aria-label="停止输出"`（原位替换，形状仍是 34px 圆形——§二点八 冻结项未动）。
- Esc 停止（约 1s 内，尚未收到内容）：**空占位被剪掉**、无「已停止展示 · 完整回答已留档」假标记、输入解锁（按钮回到 `send-btn`/「发送」）、占位文案回常态。
- 停止后再次发问正常拿到回答（不残留流式态）。
- `npm run build` 绿；lint **7 warnings / 0 errors 与 main 基线一致**。

## 诚实披露

- 「收到**部分**文本后停止」这一支**未能用真实数据触发**：演示栈走证据组装模板，回答在 <700ms 内完成，两次尝试（700ms/4s 后按 Esc）都落在完成之后。该分支现与失败路径**共用同一个 `pruneOrStopAgent()`**（失败路径的操作者/顾客两侧均已有生产用例），属代码级验证；空占位剪枝支已实测。
- 「离开页面后网络面板无继续 SSE」同样因回答瞬时完成而无法观测差异；卸载 abort 为代码级验证（`useEffect` 清理 + `client.ts` 静默返回语义）。
- 未收到任何事件即停止时，气泡被剪掉且不出现「已停止」字样（用户只看到自己那条提问）——这是「不谎称已留档」的**有意取舍**；非请求失败仍走 `onError → ErrorBanner`，不存在静默失败。

## 两轴评审与处置

独立子代理评审：**可合并，无 P0/P1**（并确认 StrictMode 双挂载不会误杀请求——挂载时 `abortRef.current === null`，赋值只发生在 `send` 里，用户请求必晚于挂载）。已修 P2：catch 与 finally 逐字重复的剪枝逻辑**抽成 `pruneOrStopAgent()` 单出处**（此前是复制，评审指出后已真去重）；closeout 口径订正（卸载 abort 作用于两页、非顾客侧专属）。

记债（P2，未改）：两个页面各自的 `Escape → stop` effect 仍是逐字重复（可上收 hook，但会顺带改 ServicePage，留待第三次消费或 UX-G）；停止时不补轻提示（见上「有意取舍」）。

## 后续

- UX-C（缺口上下文，需后端加来源会话字段）/ UX-F（薄页收口）/ UX-G（壳、确认、窄屏）在后。
- 待 Owner 裁决：工作队列首屏 183 条原始灌入货。
- 主线第 42 刀（转人工真闭环）可开工——UX-D 已先落地，`CustomerPage.tsx` 的 composer 冲突点已清。
