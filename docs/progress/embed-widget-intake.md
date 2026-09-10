# 第 45b 刀规格：可嵌入小组件 embed.js（intake + Owner 裁决）

日期：2026-09-10。分支 `feat/embed-widget`（基于 main `39e4520`）。依据：`docs/roadmap-product-hardening.md` 第 45 刀的后一半（第二梯队）。45a（令牌 TTL）已交付（PR #69）；本刀补「商家能不能真把这个客服挂到自己店里」。

## 痛点

顾客通道现在是治理台里的一个路由（`/customer`）——**商家没有任何办法把它放到自己店铺页面上**。一个不能嵌入的客服，等于只能演示。

## 裁决

| # | 裁决 | 理由 |
| --- | --- | --- |
| 1 | 加载器放 `apps/web/public/embed.js`（Vite 静态资源，**零构建、零依赖**的原生 JS） | 路线图要「~2KB 单文件 loader」；手写原生 JS 比引 esbuild 更省（本仓无前端构建链扩展预算），且可读可审 |
| 2 | iframe 指向**同应用的 `/widget` 路由**，复用 `<CustomerPage embed />`（不另起一份裁剪页） | 路线图明确允许「iframe 内复用 /customer 逻辑或裁剪版」；复用避免两套客服逻辑漂移 |
| 3 | **origin 白名单是唯一闸**：服务端设置 `WIDGET_ALLOWED_ORIGINS`（逗号分隔，**空 = 未启用**）；**不在白名单 → 403** | 路线图点名「服务端配置，非白名单 403」。闸放在**建会话端点**上（那是真正产生会话资源的动作） |
| 4 | 白名单校验用 `X-Widget-Origin` 头：由**我们自己的 widget 页**读 `document.referrer` 得出宿主来源后带上；**无该头 = 独立访问路径，行为不变** | iframe 内的请求同源，浏览器不会告诉我们父页来源；但 widget 页是我们的代码、可信读 `document.referrer`。**残留风险如实记录**：宿主可伪造该头（真要堵死需在边缘/反代拦 `/widget` 文档请求，见 README 局限段） |
| 5 | 访客身份 = **第一方 localStorage uuid**（加载器生成并保管，宿主域下），随 iframe URL 传 `visitor`，widget 在建会话时带 `X-Visitor-Id` 落库 | 抄 anythingllm-embed 模式；落在会话上操作者才看得见（否则是死字段）。**本刀不做会话续接**——顾客通道没有历史端点（既有 Out），续接会得到一个「有令牌但空对话」的假续接 |
| 6 | 访客 id 落库：新列 `service_sessions.visitor_id`（迁移 **0022**，nullable String(64)），操作者会话行显示短号 | 不做就是死字段；显示出来商家才能用自己那边的 id 对账 |
| 7 | postMessage：widget → 宿主 `{type:'ecom-ai-widget', action:'close'}`；宿主 → widget `{action:'open'}`；**targetOrigin 一律用宿主 origin，不用 `*`** | 不用 `*` 防信息泄漏到任意父页 |
| 8 | 演示宿主页 `apps/web/public/embed-demo.html`（一个假的店铺页），并把它的来源加进 compose 的 `WIDGET_ALLOWED_ORIGINS` | 没有宿主页就无法端到端验证；这也是 README 里给商家的嵌入示例 |

## Out

- 会话续接/访客识别后的历史回放（需顾客历史端点）、主题定制、多语言、文件上传、真 CDN 部署与版本号、边缘层拦截（记为部署项）。

## 验收

1. **闸**：`X-Widget-Origin` 缺失 → 行为不变（201）；白名单为空 + 带头 → 403；不在白名单 → 403（文案说清「该来源未获授权嵌入」）；在白名单 → 201。
2. **访客 id**：带 `X-Visitor-Id` 建会话 → 落库；操作者会话列表能看到；非法/超长被拒或截断（明确一种）。
3. **端到端（浏览器）**：打开 `embed-demo.html` → 出现启动按钮（Shadow DOM）→ 点击开 iframe → 问一句得到带引用的回答 → 点关闭 → iframe 收起；宿主 localStorage 有访客 uuid。
4. **独立路径不回归**：`/customer` 直接访问照旧可用（无 widget 头）。
5. 门禁：后端全量绿、ruff 净；前端 build 绿、lint 7/0。
