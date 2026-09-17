# Runbook：更换 LLM 端点（第 91 刀）

> 背景：推理走厂商 OpenAI 兼容 Chat API，单网关不做自动 fallback（每条 LLM 路径已有诚实降级——客服→证据组装模板回退、素材→failed、机洗→弃权重试、judge→fail-soft）。网关抖动/额度尽的处置=本 runbook 三步手动换端点。

## 三步

```bash
# 1. 改 .env（只动这三行）
LLM_API_KEY=<新端点 key>
LLM_BASE_URL=<新端点 base>
LLM_MODEL=<新端点模型名>

# 2. 重启 api（让容器带上新 env）
docker compose up -d --build api

# 3. 验证
#    a. 客服页问一句——回答流式输出且无「模板回退」徽章（generated 路径在线）
#    b. curl -s http://localhost:8000/health 仍 ok（key 未配错不会影响健康，靠 a 验证）
#    c. 观测佐证：GET /metrics（需 METRICS_TOKEN）里 llm_tokens_total 在增长
```

## 备用端点清单（OpenAI 兼容、免费额度，2026-09-16 调研口径）

| 端点 | base_url | 备注 |
| --- | --- | --- |
| opencode zen（默认） | `https://opencode.ai/zen/go/v1` | 当前默认；网关需 `x-opencode-session` 头的场景由 `openai` 官方包自动携带与否需实测——抖动史见 35/86 刀记录 |
| SiliconFlow | `https://api.siliconflow.cn/v1` | 注册即有免费额度；`Qwen/Qwen2.5-72B-Instruct` 等开源模型可选；国内直连快 |
| Groq | `https://api.groq.com/openai/v1` | 免费层速率限制宽松；`llama-3.3-70b-versatile` 等；大陆访问需代理（服务器在境内注意） |

选型注意：① 模型名写该端点真实在列的；② 境内服务器优先 SiliconFlow（低延迟）；③ 换完观察一天 `chat_fallbacks_total{reason}`——若 `other`（厂商失败）上涨说明新端点不稳，再换。

## 降级行为参考（不用慌的时刻表）

| 现象 | 含义 | 动作 |
| --- | --- | --- |
| 回答带「模板回退」徽章 | LLM 失败已自动降级为证据组装模板，**回答仍有引用** | 按上面三步换端点；不换也诚实可用 |
| 回流机洗 QA 抽不到 | 同上，资产停已接入可就地重试 | 换端点后点重试 |
| 素材任务 failed「生成不可用」 | 素材路径无降级（0038 纪律：失败即失败） | 换端点后重跑任务 |
