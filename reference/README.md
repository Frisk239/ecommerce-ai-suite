# Reference clones

Local study copies of open-source systems that already solve one slice of this suite. They are not dependencies. Do not import from here; steal patterns, then write our own.

Clones are gitignored. Recreate with:

```
git clone --depth 1 <url> reference/<name>
```

| Directory | Upstream | We look at it for |
| --- | --- | --- |
| `commerce-agents` | [anthropics/commerce-agents](https://github.com/anthropics/commerce-agents) | Merchant vs shopping split; MCP behind a backend interface; staged writes + human approval (运营 Agent / MCP) |
| `enthusiast` | [upsidelab/enthusiast](https://github.com/upsidelab/enthusiast) | Generic ecommerce agent toolkit shape: RAG + catalog + console, not a fake brand |
| `ai-customer-service-agent` | [asifours-blip/ai-customer-service-agent](https://github.com/asifours-blip/ai-customer-service-agent) | 客服：引用、拒答阈值、受控工具、Trace、评测集 |
| `ai-customer-service-agent-provider` | [RTY798/ai-customer-service-agent](https://github.com/RTY798/ai-customer-service-agent) | 可替换数据源（DATA_PROVIDER），对齐「种子可换」 |
| `ecommerce-retail-rag-mcp` | [abh1hi/ecommerce-retail-rag-mcp](https://github.com/abh1hi/ecommerce-retail-rag-mcp) | 中台能力打成 MCP tools（检索/库存/政策） |
| `clipforge` | [xixihhhh/clipforge](https://github.com/xixihhhh/clipforge) | 素材中心：商品图 → 脚本 → 成片流水线 |
| `stream-clipper-factory` | [Cbhhhh211/Stream-Clipper-Factory](https://github.com/Cbhhhh211/Stream-Clipper-Factory) | 直播切片：转写 → 高光打分 → 人工复核 → 导出 |
