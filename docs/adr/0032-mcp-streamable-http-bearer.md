# 连接层挂同一 FastAPI 的 Streamable HTTP；用 Bearer，不复用操作者 cookie

stdio 适合本机子进程，不能演示「外部客户端连上同一份已发布权威」。Cursor / Claude 的 MCP 客户端对 Cookie 转发不可靠，复用治理台登录会话会把发布权漏给连接层。空密钥开放等于没有闸门。导出只给 ID，外部 Agent 搜到也读不完正文。

MCP 挂在本仓库 FastAPI 同一进程的 `/mcp/`（Streamable HTTP）。鉴权是 `MCP_BEARER_TOKEN`：`Authorization: Bearer`，和操作者会话隔离；未配置或空字符串则所有 MCP 调用 401。工具四件，官方 MCP Python SDK 挂载，不用 FastMCP：

- `search_published`：只搜当前已发布（与客服同一套检索索引）
- `get_asset`：默认当前已发布版；带版本号可取历史已发布；待人洗/已接入拒绝
- `register_asset`：必须带正文；`source_kind` 由服务端定为 `mcp_registered`；落到已接入，走与治理台相同的登记骨架；不能发布
- `export_published`：当前已发布列表（展示 ID、版本、标题、种类、来源）**加该版正文全文**

没有 publish。README 给一份 Cursor `mcp.json`。不做 OAuth、不单独起 MCP 进程。本地 `.env.example` 可给仅开发用的默认 Bearer，生产必须换。
