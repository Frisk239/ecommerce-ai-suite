# Ecommerce AI Suite

商家侧电商 AI 套件：FastAPI 单体 + Vite React 控制台 + Postgres。检索默认是中文词法 bigram（ADR 0023）；compose 用 `pgvector/pgvector:pg16` 镜像，**未建向量列、未跑 embedding**。
领域决策见 `CONTEXT.md` 与 `docs/adr/`（表结构唯一依据：ADR 0022/0023）；范围与排期见 `docs/slices.md`。

## 3 分钟口述稿（演示主线）

四段带时间锚，可直接念。例子全部来自仓库种子数据与产品真实路径（保温杯/瓶装水等通用名），不绑虚构品牌。

**0:00 接待（含缺口闭环）——「答不上就不答，缺口显性给人补」**
打开客服页问「保温杯的净含量是多少」，回答 500ml，带引用芯片 `A-xxxx · v1`——引用=资产 ID+版本号，服务端定，模型没有引用决定权。紧接着问「那它的材质是什么」也接得上：会话内多轮记忆把上一轮问答带给模型做指代消解（拒答轮、工具轮不进记忆），检索词自动补全上一问，但仍然只答有证据的内容。再问一个没有已发布证据的，比如「会员积分怎么兑换？」，直接拒答并转人工：消息文本里带问句摘要和缺口编号 `G-0001`（顾客通道只见摘要，内部缺口 id 不下发，complete 载荷同样不带）。**转人工是有闭环的**（第 42 刀，ADR 0046）：明说「我要转人工」（或 找人工/要人工/人工客服/真人/投诉/举报）走引擎最前置的词表快路径直接落工单——回执给 `H-0001` 与「工作时间 4 小时内回复」的承诺话术（**话术不外发**：不真发短信/邮件），顾客可在下面留联系方式（姓名+留言必填、邮箱/电话可选、整表可跳过）；**一个会话一张工单**，操作者在客服页按「待处理工单」筛选、待处理置顶、回复后结单。订单/库存/退货资格工具查无或提议被拒同样转人工并建单——**新产生的会话凡亮「已转人工」徽章背后都有工单**（演示库里 2026-09-08 的三条历史会话是第 42 刀之前的存量、当时还没有工单表，属历史数据而非当前行为）。工单是「顾客要人」、知识缺口是「知识待补」，两者独立、同一会话可并存；没有坐席队列/分派/SLA。操作者在治理台「知识缺口」待办里点「去补文档」：上传、机洗、人洗、发布，缺口在发布事务内自动解决；同一问法再问就命中引用新资产。**顾客满意度也闭环了**（第 48 刀）：顾客打完分（页脚 1–5 星 + 可选留言，点星即提交，评过可改（覆盖式留最新，第 71 刀））后，操作者在总览「顾客满意度 · 近 7 日」看到均分 / 1–5 分布 / 最近三条**已掩码**留言，客服页会话行也带 ★ 徽章；单条回答另有「有帮助 / 没有帮助」两向反馈（正反馈只记档，负反馈才把引用资料推进复审队列）。整段会话还能回流登记成对话资产再进治理。知识变好靠发布，不靠训练。

**0:45 内容闭环——「同一份已发布权威也喂内容生产」**
素材中心选「钛钢保温杯」一键生成卖点文案，规则质检过线进待抽检，操作者抽检通过就登记为资产进中台（种类=素材），照常机洗、人洗、发布；直播侧先在切片页上传一份源录像（.mp4，≤200MB；绑错或换录像随时可「改绑」），再把转写片段拣选登记成视频资产——登记字节是 `ffmpeg` 从该源录像切出的**真 mp4 片段**（回执指名切自哪一份），转写作为版本字段供检索，汇入切片页；运营 Agent 读商品事实与已发布素材组装投放文案，操作者确认投放——「投放」和治理台的「发布」是两回事。成品与过程都进中台，可被引用、可被追溯。

**1:30 连接层（MCP）——「外部 Agent 接同一份数据，一天接一个新系统」**
同一个 FastAPI 挂 `/mcp/`（Streamable HTTP），独立 Bearer 鉴权，不复用登录 cookie。只有四个工具：`search_published` 检索已发布、`get_asset` 取已发布正文（可取历史已发布版）、`register_asset` 带正文登记（来源服务端定值）、`export_published` 全量导出已发布。**没有 publish 这个工具**——发布权只在治理台操作者手里。导出是数据包，不是微调集；本产品不做微调。

**2:20 中台为什么是核心——「答错可追溯」**
七块能力共用一个数据中台。资产只有三态：已接入、待人洗、已发布——只有已发布进检索索引，这是唯一能被 AI 引用的权威；每次引用都锚定 `A-xxxx · vN` 二元组，答错了能追回当时回答用的是哪一版字节；资产详情的血缘面板给出：从哪条来源来、被哪些提问引用过、写回了哪个商品的哪条规格、被哪场考核用过、有没有被 MCP 导出过。拒答留缺口、登记带来源、发布有审计，治理是闭环，不是七个各养一套数据的 demo。

## 快速开始（compose 一键起）

```bash
docker compose up --build
```

打开 <http://localhost:5173>：健康卡应显示 API 与数据库双绿（页面真实调用 API 的 `GET /health`）。
api 容器启动时自动跑 `alembic upgrade head` + 幂等种子（操作者、两个商品与三笔 mock 订单），无需手工迁移。

| 端口 | 服务 | 说明 |
| --- | --- | --- |
| 5432 | db | `pgvector/pgvector:pg16`（镜像预留；检索未用 pgvector） |
| 8000 | api | FastAPI 单体（治理发布后端 + `/health`） |
| 5173 | web | Vite + React 19 控制台壳 |

## 登录（0016 单操作者）

种子操作者 username `operator`，密码取 env `OPERATOR_PASSWORD`（默认 `operator123`，**仅开发用**）。
`POST /api/auth/login` 成功后签发 httpOnly 签名会话 cookie；所有业务接口（登记/确认/发布/机洗重试/客服会话与回流登记）未登录返回 401。

## 客服引用 API（第 3 刀，ADR 0023）

```bash
POST   /api/service/sessions                       # 新会话（active）
GET    /api/service/sessions                       # 列表（倒序：状态+首问摘要+消息数）
GET    /api/service/sessions/{id}                  # 详情（消息全量，含 citations/kind）
POST   /api/service/sessions/{id}/messages         # 发问 {"content": "..."} -> SSE 流
POST   /api/service/sessions/{id}/register         # 回流登记 -> kind=dialogue 资产（待人洗）
```

SSE 事件协议（`text/event-stream`，回答文本在流式开始前已完整收全落库，断连不产生 interrupted 消息，停止语义由前端表达）：

```
event: thinking    data: {"text": "正在检索已发布资产…"}
event: thinking    data: {"text": "正在生成回答…"}     # 仅模型路径；拒答/降级不出现
event: tool        data: {"name": "get_order_status", "arg": "SO-1001", "result": "已发货 · 2 个物流事件"}   # 仅工具路径（订单第 13 刀 / 库存第 14 刀）
event: delta       data: {"text": "..."}            # 多片，~12 字/片
event: complete    data: {"message_id": 1, "citations": [{"asset_id": 3, "version_no": 1}],
                          "kind": "answer", "handoff": false, "gap_id": null, "fallback": false, "tool": null}
```

订单查询带单号即命中订单工具（第 13 刀，ADR 0036）：问「我的订单 SO-1001 到哪了？」（样例单 SO-1001/1002/1003）走只读 `get_order_status`——模板组装状态回答、`citations` 恒空、`complete.tool`/`tool` 事件带工具条（两通道同形状，前端灰底 mono 参数→结果）；查无单号如 SO-9999 返回 `kind: "handoff"`、`handoff: true` 交接摘要（0018：转人工不拿检索顶，0024：不产生知识缺口）。非订单问题零漂移。**口播注意（第 78 刀）**：演示库的 SO-1001 已被第 44 刀退货流真跑过、状态停在「退货中 · 3 个物流事件」——口播稿别念「已发货」，按界面实际状态讲（这本身就是「退货确认迁移订单状态」的演示素材）。

库存问「有货吗」即命中库存工具（第 14 刀，ADR 0037）：问「钛钢保温杯有货吗？」（种子 mock 值：保温杯 42、瓶装水 0）命中词表（有货/没货/无货/缺货/库存/现货/剩）走只读 `get_stock`——商品名最长公共子串匹配（「保温杯」也能命中「钛钢保温杯」），模板回答「有货，当前库存 42 件。」/「暂时无货。」、同样不调 LLM、`citations` 恒空；库存未设置（stock NULL）、商品没匹配上（如「小龙虾有货吗」）或查询失败均 `kind: "handoff"` 转人工不检索不缺口。规格问题（净含量/保质期/材质）词表外，零漂移走检索。

无命中 -> `kind: "refusal"`、`handoff: true`、`citations: []`，固定文案「抱歉，已发布资产里没有能回答这个问题的证据。」（0018：不编造不闲聊）。检索只查当前已发布版本（0004/0017），切块在发布事务内写入 `retrieval_chunks`（0002 第二批迁移）。

拒答（0024 知识缺口）同事务落 `knowledge_gaps`（同问法精确幂等不新建），`complete.gap_id` 即缺口 id（answer 恒为 null）；`GET /api/knowledge-gaps?status=open|resolved` 看待办（全登录），「补文档」=`POST /api/assets/register` 带可选表单字段 `knowledgeGapId`（来源 `source_kind` 由端点定值：上传=upload、回流=session_backflow），发布事务内缺口自动 resolved 并指向该资产。

## 厂商生成（第 7 刀，ADR 0033）

检索命中已发布证据时，回答由厂商大模型流式生成（OpenAI 兼容 Chat Completions，`openai` 官方包配 `base_url`）；证据块（命中切块含确认字段值，各带「来源：A-{id}「资料名」·v{N}」标注——资料名供模型核验证据归属，第 81 刀修「检索命中却被误拒」）与顾客问题进 prompt。三个环境变量只写本机 `.env`（密钥不入库、不进日志/响应）：

| 变量 | 说明 |
| --- | --- |
| `LLM_API_KEY` | 厂商密钥；**为空时不建客户端、不发请求**，自动降级为证据组装模板回答 |
| `LLM_BASE_URL` | OpenAI 兼容端点（默认 `https://opencode.ai/zen/go/v1`） |
| `LLM_MODEL` | 模型名（默认 `qwen3.8-flash`） |

- **无证据不调模型**（0018）：拒答+转人工路径原样，防编造也省调用。
- **降级**：LLM 未配置/超时（20s，重试 0 次）/网络失败/空产出 -> 复用 `answer.py` 证据组装模板回答，走同一 delta 流，`complete` 事件带 `fallback: true`，前端显示「模板回退」徽章（诚实标注，不装作模型回答）；错误细节只进服务端日志且不含密钥。
- **引用服务端定**（0007）：`citations` 恒由检索命中确定，模型无引用决定权；系统提示明确要求模型不输出引用编号。
- 回答先收全再落库再流式：断连仍完整落库（契约不变）。
- **回流机洗抽 QA**（第 12 刀，ADR 0035）：配置 `LLM_API_KEY` 后，回流登记（`POST /api/service/sessions/{id}/register`）会在请求内同步等待一次 QA 抽取生成（≤20s）；LLM 失败则资产停已接入、就地重试端点可重跑，未配置则 `qa_pairs` 弃权照常待人洗。

## 顾客通道（第 8 刀，ADR 0021/0033）

`POST /api/customer/sessions` 无登录签发会话令牌（Bearer）；`POST /api/customer/sessions/{id}/messages` 发问走与控制台预览同一客服引擎；`POST /api/customer/sessions/{id}/end` 顾客结束会话（第 80 刀，幂等）——结束后发问 409，评分/反馈/联系方式仍开放（结束=关对话流不关善后），操作者仍可把已结束会话回流登记。三道限流闸（进程内滑动窗口，429+Retry-After）：IP 建会话 5/60s、IP 发问 30/60s（先于令牌鉴权——狂刷不碰库）、会话发问 10/60s（后于令牌鉴权——无效令牌耗不了真会话的配额）。会话不存在与令牌无效统一 401 同文案（自增 session id 不可探测）。传输层慢客户端防护属部署侧反代责任（应用只在返回 SSE 前释放数据库会话）。

限流 IP 口径两种部署模式（env `CUSTOMER_TRUST_PROXY`）：

| 模式 | 取值 | 语义 |
| --- | --- | --- |
| 直连（默认） | 空 / `false` | 只信 TCP 对端地址，**完全忽略 `X-Forwarded-For`**：api 直接暴露（含 compose 现状）时，伪造该请求头换不了 IP 闸 key——不配即最保守（fail-closed） |
| 反代 | `true` | 信 `X-Forwarded-For` 第一跳：api 只接反向代理流量时用，**部署者必须让反代强制覆盖/清洗该头**，否则顾客自报头即可绕过 IP 闸 |

令牌 TTL（第 45 刀）：签发即 `created_at + 24h`（env `CUSTOMER_TOKEN_TTL_SECONDS`）。**过期与无效同 401 同文案**（不向调用方区分「令牌曾有效」）；过期时刻为 NULL 视为不可用。

## 可嵌入客服小组件（第 45b 刀）

把顾客通道挂到**商家自己的站点**上：宿主页加一行脚本，右下角出现一个圆形客服按钮，点开是一个 iframe 面板。

```html
<!-- 商家页面里唯一需要加的东西 -->
<script src="https://<控制台地址>/embed.js" data-label="在线客服"></script>
```

做三件事：Shadow DOM 里的启动钮（样式与宿主页完全隔离）、首次点按才注入 iframe（`<控制台>/widget`，不给宿主首屏加负担）、postMessage 开关（面板内「收起」发消息回来；宿主也可用 `window.EcomAiWidget.open()/close()`）。访客身份是**宿主域第一方 localStorage 里的 uuid**，随 iframe 传入并落到会话上——操作者在客服页能看到「访客 xxxxxxxx」，商家可用自己那边的标识对账。**宿主站点也落库**（第 54 刀）：过闸的来源归一值记进会话，客服页会话行显示「站点 shop.example.com」——商家把 widget 挂在自己多个站点时，靠它分辨每条会话来自哪个站（独立访问没有宿主，两个字段都为空）。

**能否嵌入由服务端白名单判定（唯一闸）**：`WIDGET_ALLOWED_ORIGINS` 逗号分隔宿主 origin，**空 = 未启用嵌入**；被嵌入的页面在建会话时会带上宿主来源，**不在白名单一律 403**（本地演示默认放行了 `http://localhost:5173`，即仓库里的演示宿主页 `apps/web/public/embed-demo.html`）。样例与端到端验收就是打开那个页面。

「被嵌入」不只看 `/widget` 这一个路由：**任何被框住的上下文**（`window.self !== window.top`）都会带来源，所以第三方直接 iframe `/customer` 同样过不了闸；宿主若用 `no-referrer` 剥掉来源，客服页会**直接拒绝建会话**（fail-closed），不会静默退回独立访问。

部署注意（CSP 与残留风险）：

- 宿主页若开了 CSP，需放行 `script-src <控制台地址>`（加载 `embed.js`）、`frame-src <控制台地址>`（嵌入 iframe）与 `style-src 'unsafe-inline'`（加载器用一段内联样式做 Shadow DOM 里的按钮外观）；无需放行 `connect-src`（客服请求都发生在 iframe 内部）。
- 宿主页**不要设 `Referrer-Policy: no-referrer`**：来源取自 `document.referrer`，剥掉它客服会拒绝工作（fail-closed，不是绕过）。
- 白名单校验用的是我们前端读 `document.referrer` 后带上的 `X-Widget-Origin`。宿主若自己伪造请求头仍可能绕过，**要彻底堵死需在边缘/反代层拦文档请求**（本仓是 dev 栈，没有这层）——这里挡的是「把客服嵌进未授权站点」的正常路径。
- 同一访客的**会话续接/历史回放**不在 v1（顾客通道没有历史端点）；每次打开是全新会话。

## MCP 连接层（第 5 刀，ADR 0032）

外部 Agent（Cursor / Claude / 官方 SDK 客户端）经 Streamable HTTP 连同一份中台，端点 `http://localhost:8000/mcp/`。

- **鉴权**：`Authorization: Bearer <MCP_BEARER_TOKEN>`，与操作者登录会话完全隔离（不读 cookie）。token 未配置或为空时所有 MCP 调用一律 401；本地开发默认值见 `.env.example`（`dev-mcp-bearer`，生产必换）。
- **四工具**（没有 publish——发布只属于治理台操作者）：

  | 工具 | 语义 |
  | --- | --- |
  | `search_published(query)` | 检索当前已发布切块（与客服同一索引），返回 `{asset_id, version_no, title, chunk, score}` |
  | `get_asset(asset_id, version?)` | 取已发布资产正文；不传 version=当前指针版，传 version=历史已发布版；待人洗/已接入一律拒绝 |
  | `register_asset(content, title?, product_id?)` | 登记文档（必须带正文），来源固定 `mcp_registered`，落为已接入等治理台处理 |
  | `export_published()` | 全部当前已发布资产，含该版正文全文 |

- **冒烟**（需先有已发布资产）：

  ```bash
  docker compose up -d --build api
  MCP_BEARER_TOKEN=dev-mcp-bearer uv run python scripts/mcp_smoke.py
  # 可选参数：search 关键词、asset_id、version
  MCP_BEARER_TOKEN=dev-mcp-bearer uv run python scripts/mcp_smoke.py 保温杯 1 1
  ```

- **协议证据**：`MCP_BEARER_TOKEN=dev-mcp-bearer uv run python scripts/mcp_smoke.py --evidence`（goal §6.2.5 三断言：工具恰四无 publish、未发布 search 空、register 落已接入；pytest 版见 `apps/api/tests/test_mcp_evidence.py`）
- **Cursor mcp.json**：

  ```json
  {"mcpServers":{"ecommerce-suite":{"url":"http://localhost:8000/mcp/","headers":{"Authorization":"Bearer <token>"}}}}
  ```

- compose 端口：本机 5432 被其他项目占用时，`.env` 设 `PG_PORT`（如 `PG_PORT=5433`）后 `docker compose up -d db`，`DATABASE_URL` / `SUITE_TEST_DATABASE_URL` 同步指向该端口。

## 本地开发

前置：Python 3.12+（uv 自动管理）、Node 24+、[uv](https://docs.astral.sh/uv/)、**`ffmpeg`**（切片拣选要真切 mp4 片段；compose 的 api 镜像已装，本地裸跑需自备并在 PATH 上）。

```bash
cp .env.example .env          # 可选：本地覆盖连接串等
uv sync                       # 安装 workspace（apps/api + packages/platform）
```

- **api**（需本地或 compose 的 Postgres）：

  ```bash
  uv run uvicorn suite_api.main:app --reload --app-dir apps/api/src
  ```

  或在 compose 只起 db：`docker compose up db`，api 本地跑。
  启动 lifespan 同样会自动迁移 + 种子（幂等，存在即跳过）。

- **web**：

  ```bash
  cd apps/web
  npm install
  npm run dev                  # /api 代理到 http://localhost:8000
  ```

## 演示路径（第 78 刀：现成的好素材与要避开的）

客服页左侧有搜索框（首问/会话号，第 77 刀），以下会话号直接 `#号` 搜：

**主线素材（按演示叙事顺序）**

| 会话 | 演什么 |
| --- | --- |
| `#199` | 「你们几点上班」——**治理闭环**：拒答留缺口 → 补口径（asset 17 修订发布 v2）→ 再问命中带引用（9:00–21:00） |
| `#200` / `#202` | **多轮工具追问**：「钛钢保温杯有货吗 → 还有吗」复用上轮对象查库存（第 73 刀） |
| `#203` | **治理写回可追溯**：Vivo Y300 品牌 vivo ← `A-0269·v1`（商品页「写回来源」链接同源，第 74 刀） |
| `#125` | **类目价 + 口语**：零食/书/彩电 多少钱 → 类目聚合价；「手机壳有货吗」→ 拒答转人工（对比项） |
| `#148` | **澄清闭环**（第 70 刀）：「我的订单到哪了」→ 请提供订单号 → 补单号 → 订单详情 |
| `#198` | **评分可改**（第 71 刀）：4 星 → 填留言 → 改 5 星；详情 ★ chip 见「· 改过」（第 77 刀） |
| `#142` | **拼接追问**（审计刀 13 P0 修复）：净含量 → 「那它的材质是什么」→ 答钛钢带引用 |

**避开**（历史探针的旧形态，行为已被后续刀修掉，展示会误导）：早期「怎么退货」整段拒答的会话、「你们有笔记本吗」旧 handoff、「到货了吗」旧拒答——**避开方法**：演示前跑一次 `scripts/demo_reset.py --apply`（清空会话与探针资产），然后用上表主线会话。**另避开具名 OFF 商品的规格问句**（如「M&M white的条码」）：OFF 导入数据存在标题与正文品牌错配，同一问句会时答时拒（审计刀 16 C 轴实测 6 次 3 拒）——演示跨商品检索用「保温杯/Erdbeeren」类问句（第 79 刀实体亲和重排对这些稳定命中）。

**治理面顺带**：待处理工单与待补缺口若干（数字随演示操作累加）多为历史探针素材——演示「转人工闭环」用 `#125` 的工单即可，其余不必逐条处理；缺口演示用 `#199` 的已解决对（G4/G5）。

## 演示前检查（切片与观测）

- **切片源录像可以直接改绑**（第 49 刀）：上传新录像后，用页头的「改绑待拣候选到」下拉 +「改绑全部待拣」把候选改绑到新上传的那份（勾了候选就只改勾选的）——改绑后拣选切出的片段就来自新录像，回执会指名「已从《xxx.mp4》切出真 mp4 片段」。已登记的候选一律不动（它们的字节是历史事实，不可追改）。想回到「从零绑定」的演示起点（第 46 刀那条上传即绑的路径），把演示库复位即可：

  ```bash
  docker compose exec db psql -U suite -d suite     -c "UPDATE clip_candidates SET recording_id = NULL WHERE status = 'pending';"
  ```

  （录像本身会留在对象存储里成为孤儿——录像是切片模块自有的输入源，没有删除端点，这是有意的。）
- **`/metrics` 默认是关的**：不设 `METRICS_TOKEN` 一律 401。要现场演示抓取，三步缺一不可：①把自选令牌写进本机 `.env` 的 `METRICS_TOKEN=`（审计刀 13 C 轴：清单原只写 printf 一步，变量未设时会生成**空** token 文件，Prometheus 全程 401）；②`docker compose up -d --build api` 让容器带上它；③`printf '%s' "$METRICS_TOKEN" > ops/metrics_token`（与 ① 同值）再 `--profile metrics up`。
- **ffmpeg 必须在**：compose 的 api 镜像已装、CI 显式安装；本机裸跑需自带（缺失时拣选报 422「ffmpeg 无法执行」，不是静默降级）。
- **探针数据（第 61 刀）**：冒烟、审计与端到端探针会在演示库留下空会话与探针资产（客服页一堆「未开始」、资产列表里 `mcp-smoke` / `evidence probe` 机器行）。演示前清一次（命令里的 5433 是本机端口：宿主 5432 被占用时 `.env` 设 `PG_PORT=5433` 覆盖，端口随它走——`.env` 的 `DATABASE_URL` 也要用同一端口，否则脚本/迁移会连错库）：

  ```bash
  uv run python scripts/demo_reset.py --db postgresql://suite:suite@localhost:5433/suite            # 只报告（默认 dry-run，不写库）
  uv run python scripts/demo_reset.py --db postgresql://suite:suite@localhost:5433/suite --apply    # 真清
  ```

  它清**两类特征行**（空会话：无消息/工单/评分/缺口/回流锚；探针资产：`mcp_registered` 且标题 `^mcp-smoke|^evidence probe`（大小写不敏感，与前端判据对齐）→ 置 discarded 不删行）。**请在演示开始前跑**：顾客刚创建、还没发第一问的会话也符合「空会话」判据，会被一并删掉（其下一问会 401）。**知识缺口与工单只报告不清**——那些是「拒答留缺口 → 去补 → 再问命中」与「转人工闭环」的演示素材。默认 dry-run、必须显式 `--apply`。

## 数据来源与演示价（第 50 / 55 刀）

演示库里有**四份真实数据集**，它们在产品面上的来源是可见的（资产来源列 / 商品来源 chip）：

| 数据 | 量 | 落在哪 | 产品面显示 |
| --- | --- | --- | --- |
| Wikidata 商品（`scripts/realdata/fetch_wikidata_products.py`） | 91 | `products` | 商品卡「Wikidata」 |
| OpenFoodFacts（`load_openfoodfacts.py`） | 20 商品 + 20 规格资产 | `products` / `assets` | 商品卡与资产来源「OpenFoodFacts」 |
| 在线购物评论（`load_reviews.py`） | 200 资产 | `assets` | 资产来源「评论导入」 |
| WANDS 家具检索基准（`load_wands_clips.py`） | 30 切片候选 + 1 承载商品 | `clip_candidates` / `products` | 承载商品「WANDS 基准」+ 切片候选卡各自的源录像标签 |

许可与出处见 `scripts/realdata/README.md`；**来源是只读字段**（既成事实，运营改不了——可改就成可造假的溯源）。

**商品价一律是演示价**：`services/seed.py` 的 `CATEGORY_DEMO_PRICES`（食品 3 元 / 器皿 129 元 / 图书 59 元 / 家具 899 元 / 笔记本 4999 元 / 手机 2999 元 / 平板 1999 元 / 电视 3499 元 / 洗衣机 2199 元）是 **mock 数据、非真实售价**，迁移 0026 只给未定价的行回填、不覆盖手改价。顾客侧问价直接答实时行价（「钛钢保温杯多少钱？」→「售价 129元」；报价是工具式回答、不带引用）；「你们卖什么」列已定价前 8 件。

## 观测（第 47 刀）

- **日志**：structlog JSON 打到 stdout（既有 `getLogger(...)` 调用一行未改，渲染层统一）；`LOG_LEVEL` 调级别，默认 INFO。
- **关联 id**：每个响应带 `X-Request-Id`，同值进每条 JSON 日志的 `correlation_id` 字段——一行一问能拼回一条请求链。客户端传合规 id（8–64 位 `[A-Za-z0-9._-]`）则沿用，否则服务端生成（脏值不原样回显）。
- **指标**：`GET /metrics`（Prometheus 文本格式），**独立 Bearer**——设 `METRICS_TOKEN` 才可用，**留空一律 401**（默认栈不裸奔），不接受登录 cookie。内容 = HTTP RED（`http_requests_total` / `http_request_duration_seconds`，`/metrics` 与 `/health` 自身不入账）+ 七个业务指标：`chat_requests_total{channel,kind,generated}`（`generated=false` 即模板/工具回答，给出**模板回退率**）、`ttft_seconds`（请求进入 → 厂商首个增量，只记生成路径）、`llm_tokens_total{direction,model}`（厂商 usage，缺了不记、不用字数估算冒充）、`clip_cuts_total{result}`（切片拣选：真切成功 / 切段失败 / 无源录像走旧文本路径，审计刀 9 补）、`csat_ratings_total{score}`（会话评分分布，审计刀 9 补）、`chat_fallbacks_total{channel,reason}`（**闸回退**：coverage=忠实度闸降级 / no_coverage=证据未覆盖按拒答收口 / oov=实体不在库按拒答收口 / other=防御位；普通厂商失败不计——第 63 刀补、第 82 刀补 oov，审计刀 7 起记债。**闸回退率 = 本指标 / `chat_requests_total`，不是 `generated=false` 占比**——后者把厂商失败降级与工具/目录回答都算进来）、`service_session_transitions_total{from,to}`（会话生命周期迁移：new->active 建会话 / active->ended 顾客结束 / active\|ended->registered 回流——第 84 刀补，审计刀 16 记债；标签值有界、不带会话 id）。
- **抓取（可选，默认不启）**：**先建令牌文件再起**——`printf '%s' "$METRICS_TOKEN" > ops/metrics_token`（与 api 的 `METRICS_TOKEN` 同值；该文件已 gitignore，模板见 `ops/metrics_token.example`），然后 `docker compose --profile metrics up` → Prometheus 起在 <http://localhost:9090>，配置 `ops/prometheus.yml`。两点环境事实：①Prometheus **不展开**配置文件里的 `${VAR}`，故令牌只能走 `credentials_file` 挂文件；②缺该文件时 Docker 会把源路径建成同名**目录**，表现为 target down（不是 401）。
- 口径、标签基数纪律与 Out（不接 OTel/trace 传播、无面板/告警/远端写）见 `docs/progress/observability-intake.md`。

## 数据库迁移（Alembic）

迁移挂在 `apps/api`（`alembic.ini` + `migrations/`）；连接串读 env `DATABASE_URL`：

```bash
DATABASE_URL=postgresql://suite:suite@localhost:5432/suite uv run alembic -c apps/api/alembic.ini upgrade head
DATABASE_URL=postgresql://suite:suite@localhost:5432/suite uv run alembic -c apps/api/alembic.ini downgrade base   # 可逆性验证
```

应用启动时会程序化执行同一迁移（`command.upgrade(cfg, "head")`），命令行方式供运维/排查使用。

## 跑测试

```bash
uv run pytest         # 单元：/health 冒烟 + 机洗抽取边界 + 发布闸门分类 + 会话签名 + 对象存储
uv run ruff check .   # lint（line-length 100）
```

### 集成测试（需真 Postgres）

先起 db，再设 `SUITE_TEST_DATABASE_URL`（测试自建 `suite_test` 库、结束自清理；未设则 skip）：

```bash
docker compose up -d db
SUITE_TEST_DATABASE_URL=postgresql://suite:suite@localhost:5432/suite_test uv run pytest
```

覆盖全链路：登录 → 上传（真写临时对象存储目录）→ 机洗抽到/弃权 → 未确认发布 422（缺项分「缺少」与「未确认」两类）→ 确认/补填 → 发布 200 → 商品 spec_values 写回带 `asset_id·version` 来源 → 审计留痕 → 未登录 401 → 机洗失败/就地重试 → 对象键形状。
客服刀闭环：发布（切块入索引）→ 问「净含量」SSE 引用 v1 → 问待人洗独有内容 refusal+handoff → 回流登记 dialogue 资产待人洗 → 发布 → 再问命中引用该对话 → 版本跟随指针（指针前移后命中 v2 块）。

### 评测集（golden conversations + policy edges）

数据在 `apps/api/evals/golden.json`，runner 在 `apps/api/tests/test_eval_set.py`（ADR 0027：评测集=问题+期望的可复现记录，不是资产、不建表、不做评测台）。它随集成测试跑：无 DB 时 runner 用例自动 skip、schema 自检照常跑；CI 位即上面两条 `uv run pytest`（`-k eval_set` 可单跑）。

加一条 case 只改 JSON 不改代码：`{"id": "...", "question": "...", "expect": ...}`，expect 三形状之一——`{"cite_asset_title": "...", "version_no": 1}`（该引用哪条已发布资产）、`{"refuse": true}`（该拒答+转人工）、`{"tool": "order"|"stock", "tool_summary_contains": "...", "handoff": true?}`（该走哪个工具；查无/未命中转人工加 `"handoff": true`）。引用类新标题需同时在 runner 的 `anchors` fixture 里发布同标题资产（schema 自检验字段形状；标题没配套资产时 runner 会指名报错）。

锚定口径是**标题不锚 id**：测试库每个 module 独立自建 `suite_test` 库，资产 id 随发布顺序漂移，只有标题跨运行稳定可复现；runner 在 fixture 内记录 `{标题: asset_id}` 再把 JSON 里的标题解析成 id 断言引用。

web 构建校验：`cd apps/web && npm run build && npm run lint`

## 环境变量

见 `.env.example`：`DATABASE_URL`、`STORAGE_ROOT`、`OPERATOR_PASSWORD`（种子操作者密码，默认 operator123 仅开发）、`SESSION_SECRET`（会话 cookie 签名密钥，生产必换）、`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（OpenAI 兼容 Chat Completions，只写本机 `.env`，禁止入库）、`MCP_BEARER_TOKEN`（连接层独立凭证，空则 MCP 全部 401，不复用登录 cookie）、`CUSTOMER_TRUST_PROXY`（顾客通道 XFF 信任模式，空=直连忽略 XFF，反代部署设 true，语义见「顾客通道」节）、`CUSTOMER_TOKEN_TTL_SECONDS`（顾客会话令牌有效期，默认 86400=24h）、`WIDGET_ALLOWED_ORIGINS`（可嵌入小组件的宿主白名单，逗号分隔，**空=未启用嵌入**，见「可嵌入客服小组件」节）。真实 LLM 密钥只落到 `.env`。

## 仓库布局

```
apps/api            FastAPI 单体（suite_api，src 布局 + alembic migrations）
apps/web            Vite + React 19 控制台壳（suite-web）
packages/platform   中台基础库（suite_platform：对象存储端口 + 本地实现）
docs/               领域与 ADR（只读）
prototype/          冻结的产品原型（只读）
```
