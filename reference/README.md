# Reference clones

Local study copies of open-source systems that already solve one slice of this suite. They are not dependencies. Do not import from here; steal patterns, then write our own.

Clones are gitignored. Recreate with:

```
git clone --depth 1 <url> reference/<name>
```

| Directory | Upstream | We look at it for |
| --- | --- | --- |
| `commerce-agents` | [anthropics/commerce-agents](https://github.com/anthropics/commerce-agents) | Merchant vs shopping split; MCP behind a backend interface; staged writes + human approval (运营 Agent / MCP) |
| `enthusiast` | [upsidelab/enthusiast](https://github.com/upsidelab/enthusiast) | Generic ecommerce agent toolkit shape: RAG + catalog + console, not a fake brand; Shopify Admin API 商品目录/文档源同步（数据接入刀参考） |
| `ai-customer-service-agent` | [asifours-blip/ai-customer-service-agent](https://github.com/asifours-blip/ai-customer-service-agent) | 客服：引用、拒答阈值、受控工具、Trace、评测集 |
| `ai-customer-service-agent-provider` | [RTY798/ai-customer-service-agent](https://github.com/RTY798/ai-customer-service-agent) | 可替换数据源（DATA_PROVIDER），对齐「种子可换」 |
| `ecommerce-retail-rag-mcp` | [abh1hi/ecommerce-retail-rag-mcp](https://github.com/abh1hi/ecommerce-retail-rag-mcp) | 中台能力打成 MCP tools（检索/库存/政策） |
| `clipforge` | [xixihhhh/clipforge](https://github.com/xixihhhh/clipforge) | 素材中心：商品图 → 脚本 → 成片流水线 |
| `stream-clipper-factory` | [Cbhhhh211/Stream-Clipper-Factory](https://github.com/Cbhhhh211/Stream-Clipper-Factory) | 直播切片：转写 → 高光打分 → 人工复核 → 导出 |
| `web-widget` | [Heltar/web-widget](https://github.com/Heltar/web-widget) | 嵌入 widget 的**无 iframe 派**：单 `<script>` 粘贴、Shadow DOM Custom Element 做 CSS 隔离、第一方 localStorage 访客 id、服务端唯一门禁是 Origin allowlist（顾客通道 widget 刀） |
| `anythingllm-embed` | [Mintplex-Labs/anythingllm-embed](https://github.com/Mintplex-Labs/anythingllm-embed) | 嵌入 widget 的 **embed-id 派**：`data-embed-id` + 随机会话 id、每 embed 总量与每会话消息数限额（对应本仓 IP 限流）、公开聊天接口形状（顾客通道 widget 刀） |
| `faq-extract` | [JackPriceBurns/faq-extract](https://github.com/JackPriceBurns/faq-extract) | 对话→FAQ 五步流水线：GPT 抽问 → 向量嵌入 → 余弦 ≥70% 聚组 → 每组取后文上下文 → 生成精修 QA 对；与 AI Knowledge Assist（arXiv:2510.08149）同构（回流增强刀） |
| `qa-extraction-with-human-review` | [eggai-tech/qa-extraction-with-human-review](https://github.com/eggai-tech/qa-extraction-with-human-review) | QA 草稿**人审工作流**：Label Studio 审核界面、每条 QA 带源引用（行号/切块）、质量过滤与去重阈值——「模型拟、人签」的工程化（回流增强刀） |
