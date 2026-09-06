# FastAPI 单体智能后端 + Vite React 控制台

中台、机洗、客服、MCP、以后的切片/微调都住在 Python。控制台是登录后的操作台，不需要 SSR/SEO。TS 全栈再挂 Python 微服务会把中台接口拆成跨语言 RPC，第 1 刀就要三个运行时，也和「内部走中台接口、先不拆部署单元」冲突。

`apps/api`（FastAPI）承担中台接口和以后的 MCP 挂载。`apps/web`（Vite + React + TS）只通过 HTTP/SSE 调 API。Postgres + pgvector 与本地对象存储接口在 API 进程后。GPU 重活以后用同语言 worker，不先开 Python 微服务，也不用 Next 当 BFF。
