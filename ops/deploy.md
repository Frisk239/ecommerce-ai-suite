# 部署手册：自有服务器 · IP + HTTP（第 91 刀）

> 形态口径（Owner 2026-09-16 裁决）：**IP 直连，无域名、无 HTTPS、无反代**。这是一台演示栈：反代/XFF 清洗/慢客户端防护按 IP 直连语义**不适用**（非欠账）；升级到 HTTPS 的路径见文末。**明文 HTTP 边界：不放真实顾客个人数据**（联系方式表单可以收演示数据，不得当真实客服工单系统用）。

## 0. 前置（服务器上）

- Linux x86_64，已装 Docker Engine ≥ 24 与 docker compose 插件（`docker compose version` 有输出）。
- 开放端口：`5173`（web 控制台+宿主页）、`8000`（api，含 `/mcp/`）。**不要开放 `5432`**（数据库只留在 compose 内网；云安全组/防火墙封掉，见安全 checklist #6）。
- 本地仓库一份（用于跑 realdata 灌入脚本，见 §4）。

## 1. 起栈

```bash
git clone https://github.com/Frisk239/ecommerce-ai-suite.git && cd ecommerce-ai-suite
cp .env.example .env
# ↓ 按 §2 逐项改 .env，改完再起
docker compose up -d --build
```

api 容器启动时自动跑 `alembic upgrade head` + 幂等种子（操作者、两商品、三笔 mock 订单），无需手工迁移。

## 2. 生产 .env 清单（逐项）

| 变量 | 生产取值 | 说明 |
| --- | --- | --- |
| `OPERATOR_PASSWORD` | **强密码**（≥16 位随机） | 默认 operator123 仅开发，公网上必须换 |
| `SESSION_SECRET` | 随机 ≥32 字节（`openssl rand -hex 32`） | 会话 cookie 签名密钥 |
| `MCP_BEARER_TOKEN` | 随机 ≥32 字节 | 留空=所有 MCP 调用 401（fail-closed），要用就设强值 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 厂商值 | 只写服务器 `.env`，**不入库不进日志**；换端点见 `ops/runbook-llm.md` |
| `WIDGET_ALLOWED_ORIGINS` | `http://<服务器IP>:5173` | 嵌入白名单（逗号分隔可多个）；**空=嵌入未启用**（fail-closed） |
| `CUSTOMER_TRUST_PROXY` | **留空**（直连模式） | IP 直连下完全忽略 `X-Forwarded-For`，最保守 |
| `CUSTOMER_TOKEN_TTL_SECONDS` | 默认 86400 即可 | 顾客令牌 24h |
| `METRICS_TOKEN` | 留空（默认） | 空=`/metrics` 恒 401——公网演示不抓指标就别开；要开见 `ops/prometheus.yml` 与 metrics_token 文件三步 |
| `PG_PORT` | 服务器 5432 空闲则不设 | 仅当宿主 5432 被占时覆盖（并同步 DATABASE_URL 端口） |
| `DATABASE_URL` | 默认（容器内网） | 灌库脚本从外部连时才改成宿主可达形式 |

## 3. 验证（起栈后 3 分钟）

1. 浏览器开 `http://<IP>:5173`：健康卡 **API 与数据库双绿**（页面真调 `GET /health`）。
2. 用新 `OPERATOR_PASSWORD` 登录成功。
3. 客服页问一句「显示器有货吗」→ 库存工具聚合回答（数码店数据就位时）。
4. 开 `http://<IP>:5173/storefront.html`（数码外设店宿主页）→ 右下角客服按钮 → 点开能聊（过了 origin 白名单闸）。
5. `curl -s http://<IP>:8000/health` 返回 ok；`curl -si http://<IP>:8000/mcp/` 无 Bearer 得 401。

## 4. 演示数据灌入（真实开放数据集，许可可指）

服务器是空库时，从**本地**仓库跑 realdata 脚本指向服务器库（临时开放 5432 或 SSH 隧道 `ssh -L 5433:localhost:5432 user@server` 后 `--db postgresql://suite:suite@localhost:5433/suite`）：

```bash
docker compose up -d db                                    # 本地起 db（幂等，脚本要 suite_api 模型）
HTTPS_PROXY=... uv run python scripts/realdata/fetch_wikidata_products.py --load --db <服务器库URL>
HTTPS_PROXY=... uv run python scripts/realdata/fetch_wikidata_products.py --digital-only --load --db <服务器库URL>
uv run python scripts/realdata/load_openfoodfacts.py --load ...           # 形态见 scripts/realdata/README.md
uv run python scripts/realdata/load_reviews.py --cats 平板,计算机,手机 --n 200 --import --publish 20 --db <服务器库URL>
uv run python scripts/realdata/publish_digital_specs.py --db <服务器库URL>
```

脚本清单、参数与幂等表见 `scripts/realdata/README.md`；政策口径三份与商品图走治理台上传通道（人工动作，即「开店」本身）。探针清理用 `scripts/demo_reset.py`（默认 dry-run）。

## 5. 安全 checklist（上线前逐项勾）

- [ ] `OPERATOR_PASSWORD` 已换强密码（非 operator123）
- [ ] `SESSION_SECRET` 已设随机值
- [ ] `MCP_BEARER_TOKEN` 已设强值（或明确不用 MCP 保持 401）
- [ ] `METRICS_TOKEN` 留空（`/metrics` 恒 401）
- [ ] `WIDGET_ALLOWED_ORIGINS` 只含自己的 origin，不含通配
- [ ] 云安全组/防火墙：**5432 不对公网开放**（compose 的 db 端口映射仅本机/内网用）
- [ ] `CUSTOMER_TRUST_PROXY` 留空（直连，忽略 XFF）
- [ ] 服务器系统更新过；SSH 走密钥非密码（通用基线，非本栈特有）
- [ ] 已读明文 HTTP 边界：**不放真实顾客个人数据**
- [ ] 备份策略：`docker compose exec db pg_dump -U suite suite > backup-$(date +%F).sql` 至少每周一次

## 6. 已知边界（如实告知，非欠账）

- **明文 HTTP**：会话 cookie 与顾客令牌在公网链路可被截获——演示栈接受；上真实数据前必须 HTTPS（§7）。
- **X-Widget-Origin 头伪造**：白名单挡的是「正常嵌入路径」；宿主自伪造请求头可绕（README widget 节已述）——彻底堵死需边缘层，本形态不做。
- **慢客户端**：应用在返回 SSE 前已释放数据库会话；传输层慢客户端防护属反代责任，IP 直连形态不适用。
- **单操作者**：无 RBAC，治理台全权在 operator 一个账号上（ADR 0008/0016，v1 决策）。

## 7. 升级路径（未来要 HTTPS 时）

最小改动是加一层 Caddy（自动证书需域名）：`Caddyfile` 两行反代 5173/8000 + `CUSTOMER_TRUST_PROXY=true`（此时 XFF 清洗由 Caddy 强制覆盖）。届时本文 §5 的 XFF 项随之切换。**不做就不做全套**——半吊子反代比直连更危险。

## 8. 常见故障

| 症状 | 处置 |
| --- | --- |
| 健康卡 db 红 | `docker compose logs db`；宿主 5432 冲突→`PG_PORT=5433` 重起 |
| 客服全走「模板回退」 | LLM 网关不可用/额度尽 → `ops/runbook-llm.md` 换端点三步 |
| storefront 点开客服 403 | `WIDGET_ALLOWED_ORIGINS` 没含该页 origin → 补值后 `docker compose up -d --build api` |
| 顾客建会话 429 | 三道限流闸触发（正常防护）；等窗口或查 `rate_limit` 日志 |
