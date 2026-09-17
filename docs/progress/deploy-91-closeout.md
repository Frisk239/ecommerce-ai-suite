# 第 91 刀 closeout：部署刀（IP + HTTP 演示栈工件）

日期：2026-09-16。分支 `feat/deploy-91`（stacked 链 …→#127（90 刀）→#128（roadmap 修订）→本刀）。

## 交付

1. **`ops/deploy.md`**：部署手册——前置与端口（5173/8000 开、**5432 不公网**）、起栈、**生产 .env 逐项清单**（强密码/SESSION_SECRET/MCP token/WIDGET_ALLOWED_ORIGINS/CUSTOMER_TRUST_PROXY 留空=直连 fail-closed/METRICS_TOKEN 留空=401）、5 分钟验证清单、演示数据灌入（realdata 命令指向服务器库的两种连法）、**安全 checklist 10 项**、已知边界如实告知（明文 HTTP/头伪造/慢客户端/单操作者）、HTTPS 升级路径（Caddy 两行）、故障处置表。
2. **`ops/runbook-llm.md`**：换端点三步（改 .env→`up -d --build api`→验证）+ 备用免费端点清单（opencode zen 默认 / SiliconFlow / Groq）+ 降级行为参考表。多供应商自动 fallback **不立项**（Q10 裁决：每条 LLM 路径已有诚实降级）。
3. **`apps/web/public/storefront.html`**：数码外设店宿主页示例——店铺头/商品卡/一行 `embed.js` 引入；注释标注 Referrer-Policy 红线（no-referrer 会 fail-closed）。
4. **README** 快速开始节加部署文档/runbook/storefront 指向。

## 验收证据（真浏览器实跑，ZCode 内置浏览器）

| 步骤 | 结果 |
| --- | --- |
| 宿主页加载 `http://localhost:5173/storefront.html` | ✓ 店铺头/商品卡/说明正确渲染 |
| 「在线客服」按钮注入（Shadow DOM） | ✓ 右下角按钮出现 |
| 点开 widget 面板（iframe） | ✓ 「AI 客服 · 开始咨询」 |
| 开始咨询 → 会话建立 | ✓ 面板进入会话态（推荐问句/输入框/评分条） |
| **问「显示器有货吗」** | ✓ 回答「显示器共 22 件，其中有货 21 件，库存合计 1085 件」+ 工具记录条 `get_stock(显示器)` + CSAT 评分条 |

- 前端门禁：`npm run lint` **7 warnings / 0 errors**（与 main 基线一致）；`npm run build` 绿。
- 角色说明：宿主页在宿主 origin（localhost:5173）被 compose 默认白名单放行——安装级验证走的是真闸门全链（来源头→白名单→建会话→引擎）。

## 偏差

1. **IAB 浏览器对 Shadow DOM + iframe 组合的 actionability 误报**：`frameLocator` 内按钮 click 报「covered by <button>」但 `elementFromPoint` 证明点击点即按钮本体（`same: true`）——交互层问题非产品缺陷（real Chrome 人工点击正常；45b 刀验收已证）。本轮以页面内 `el.click()`（事件真实冒泡）等价完成点击验收。
2. **服务器实挂未做**：按 Owner 裁决本刀交付工件，实挂待 Owner 在服务器执行（deploy.md §1–§3 即操作清单）。

## 记债

- 无新增。90 刀 `#90-P1×2`（弱命中/块排序）继续挂 101 刀样本池。

## 后续

第 92 刀使用剧本Ⅰ（数码店已开：治理全流程真实走 + 真实问法 50+ + 飞轮一圈 + MCP 真连 + 使用日志）。
