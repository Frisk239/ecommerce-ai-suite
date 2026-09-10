# 第 45b 刀 closeout：可嵌入客服小组件（`feat/embed-widget`）

日期：2026-09-10。规格 `docs/progress/embed-widget-intake.md`（Owner 八条裁决）；依据 `docs/roadmap-product-hardening.md` 第 45 刀的后一半。**第 45 刀至此走完**（45a 令牌 TTL 见 `token-ttl-closeout.md`）。迁移 **0022**。

## 交付

| 件 | 交付 |
| --- | --- |
| 加载器 | `apps/web/public/embed.js`：原生 JS、零依赖零构建；Shadow DOM 里一个圆形启动钮（`:host{all:initial}` 与宿主页样式完全隔离）；**首次点按才注入 iframe**（不给宿主首屏加负担）；postMessage 开关（面板内「收起」发消息回来；宿主也可 `window.EcomAiWidget.open()/close()`）；**幂等**（同页重复加载只生效一次） |
| widget 页 | `/widget` 路由复用 `<CustomerPage embed />`——同一份客服逻辑，只是收窄布局（无页面说明文字、满高、多一个「收起」） |
| 来源闸 | `WIDGET_ALLOWED_ORIGINS`（逗号分隔宿主 origin，**空 = 未启用嵌入**）：被嵌入的页面在建会话时带宿主来源，**不在白名单一律 403**；闸放在**限流之前**（纯 settings 读取，未授权来源连配额都不吃） |
| 访客 id | 宿主域第一方 `localStorage` 的 uuid（加载器生成并保管）→ iframe URL → 建会话时落 `service_sessions.visitor_id`（迁移 0022）→ 操作者会话行显示「访客 xxxxxxxx」；**只在嵌入请求上收**（独立访问自带该头也不落） |
| 演示宿主页 | `apps/web/public/embed-demo.html`：一个假的店铺页，右下角客服就是这一行脚本；compose 默认放行 `http://localhost:5173` 即它 |
| 文档 | README 新增「可嵌入客服小组件」段：一行嵌入代码 + **CSP 放行清单**（`script-src` / `frame-src` / `style-src 'unsafe-inline'`）+ 局限（伪造头、referrer 剥离、无会话续接） |

## 验收（端到端，真浏览器）

1. 打开 `embed-demo.html` → Shadow DOM 里出现「在线客服」启动钮（默认收起）。
2. 点开 → iframe 指向 `/widget?visitor=<uuid>`，**宿主 localStorage 里有访客 uuid**，widget 页在 iframe 内渲染。
3. 在 widget 内「开始咨询」→ **建会话成功**（白名单放行）→ 提问「保温杯的净含量是多少」→ 答「净含量为 500ml。[1]」+ 引用 `A-0009`。
4. 点 widget 内「收起」→ 宿主侧 postMessage 收到、面板收起（`data-open=0`）。
5. 库里该会话 `visitor_id` = 宿主 uuid；操作者客服页显示「访客 43bf8b95」。
6. **拒绝路径**：把白名单换成不匹配的来源重启 api → 同一路径下 widget 收到 403，界面显示「来源 http://localhost:5173 未获授权嵌入本站客服」，**没有输入框**（会话建不出来）；恢复白名单后主路径照常。

后端钉测 `test_widget_embed.py` 7 例：独立访问不受影响（且访客 id 为 NULL）/ 白名单为空 → 403（文案「未启用嵌入」）/ 不在白名单 → 403（文案点名来源）/ 在白名单 → 201（含尾斜杠归一）/ 访客 id 落库并在操作者列表可见 / 超长截断（纯函数直测）/ **独立访问自带 `X-Visitor-Id` 也不落库**。

门禁：集成 **795 → 802 passed / 0 failed / 0 skipped**；ruff 全过；前端 build 绿、lint **7/0** 与 main 基线一致。

## 评审处置（这一刀评审价值很高）

独立子代理评审无 P0，但报了两个**真 P1**，都修了：

- **P1「唯一闸」名不副实**：原先只有带 `X-Widget-Origin` 的请求过闸，任何站点直接 iframe `/customer`（不带该头）就能免费嵌入；而我 README/注释把它说成了唯一闸。修法：**前端把「被框住」（`window.self !== window.top`）作为嵌入判定**，不只看 `/widget` 路由——第三方 iframe 我们的 `/customer` 一样会被服务端 403；并且宿主用 `no-referrer` 剥掉来源时**不再静默退回独立访问**，而是直接拒绝建会话（fail-closed，界面写明理由）。
- **P1 闸序与注释自相矛盾**：`_widget_gate` 放在限流之后、注释却写「未授权来源不该吃配额」。修法：纯 settings 读取的闸**提到限流之前**。

P2 一并修：独立访问不再收 `X-Visitor-Id`（否则脏数据）/ 加载器加幂等保护 / demo 页里字面 `**` 修掉 / README 的 CSP 清单补 `style-src 'unsafe-inline'`（漏了会白屏）与「宿主不要设 no-referrer」的部署注意。

## 诚实披露

- **白名单不是绝对安全**：来源由前端读 `document.referrer` 后带上，宿主若自己伪造请求头仍可能过；要彻底堵死需在边缘/反代层拦文档请求（本仓是 dev 栈，没有这层）。README 局限段与 `_widget_gate` 的 docstring 都写明了，没有把它说成不可绕过。
- **无会话续接**：同一访客再次打开是全新会话（顾客通道没有历史端点，那是有意的 Out）——访客 id 的用途是让商家对账，不是恢复上下文。
- 加载器在老浏览器（无 `attachShadow`）退化为 light DOM：功能不缺，样式隔离打折（现代浏览器都走 Shadow DOM）。
- 端到端证据是**本会话实测输出**（上列 6 条），未落成截图/脚本文件。

## 后续

- 第 45 刀（45a + 45b）至此走完 → 按路线图节奏开 **审计刀 8**（第 45 刀后；三路并行只读审计 + P0/P1 实修）。
- 仍待 Owner 裁决：工作队列首屏 183 条原始灌入货的呈现方式。
