# 连接层挂同一 FastAPI 的 Streamable HTTP；用 Bearer，不复用操作者 cookie

stdio 适合本机子进程，不能演示「外部客户端连上同一份已发布权威」。Cursor / Claude 的 MCP 客户端对 Cookie 转发不可靠，复用治理台登录会话会把发布权漏给连接层。

MCP 挂在本仓库 FastAPI 同一进程的 `/mcp/`（Streamable HTTP）。鉴权是环境变量里的 Bearer，和操作者会话隔离。工具仍是检索 / 取已发布（可按版本取历史已发布）/ 登记 / 导出；没有 publish。README 给一份 Cursor `mcp.json`。不做 OAuth、不单独起 MCP 进程。
