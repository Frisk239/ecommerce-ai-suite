# Closeout · 工程第 4 刀：知识缺口闭环（feat/knowledge-gap）

- 日期：2026-09-06
- 分支：`feat/knowledge-gap`（stack 于 feat/service-citation / PR #5 未合并）：`3b1ee57` ADR 0030 → `5206782` 实现 → 评审修复 → `98ded04` 方向更新包（计划者产物，随刀入库）→ 本 closeout
- 短对齐：`.scratch/knowledge-gap/spec.md`（本地）

## 交付（飞轮闭环全程）

操作者客服页问「会员积分怎么兑换？」→ 拒答+转人工，拒答消息下**「知识缺口 G-0001」芯片** → 跳资产列表「知识缺口」tab（open 待办）→「去补文档」→ 登记抽屉预填（`补口径 · 原问`+关联缺口）→ 上传→机洗→发布（不挂商品无必填闸门）→ **发布事务内缺口 resolved（「已由 A-0002 解决」芯片）** → 同一问法再问 → 命中回答带 `A-0002 · v1`。

- **表**（ADR 0030）：`knowledge_gaps`（精确幂等：同问 open 复用）；`assets.source_kind` 六枚举 NOT NULL（存量回填 upload）。
- **拒答落缺口**（0024/0031）：仅 refusal 路径（answer 对照契约测试钉死）；SSE complete 增 `gap_id`；会话回流独立不受影响。
- **source_kind**（0025）：服务端定值（register=upload、回流=session_backflow），调用方不可传；列表/详情带来源（0026 血缘第一环）。
- **缺口关闭**（0031）：登记可带 `knowledge_gap_id`（登记后指向仍 open，**发布事务内** resolved）；一个 open 缺口同时只挂一份补文档（二次登记 409——评审修复）；无手动关闭端点。
- **UI**：五 tabs（全部/已接入/待人洗/已发布/知识缺口，`?status=` 同步）；缺口表（G-0001/原问/商品/待补·已解决/去补文档/解决芯片）；拒答缺口芯片；ID 全站 `A-0001/G-0001` 四位格式（库内仍 int）。

## 证据（Owner 亲跑/点穿，输出原文）

| 项 | 输出 |
| --- | --- |
| `uv run pytest` | `100 passed, 25 skipped, 2 warnings in 6.42s` |
| 集成（SUITE_TEST_DATABASE_URL） | `125 passed, 16 warnings in 12.89s`（123+评审修复 2；5432 被本机另一项目占用，修复轮经 5433 等效临时库验证） |
| `uv run ruff check .` | `All checks passed!` |
| web build / oxlint | `✓ built` / `2 warnings, 0 errors`（既有） |
| 浏览器点穿（IAB） | 拒答→G-0001 芯片→缺口 tab 待办→预填抽屉→登记（source=upload）→发布→缺口「已解决 · 已由 A-0002 解决」→再问命中 `A-0002 · v1` |
| 迁移 | 0003 up/down/up 可逆；存量回填有专测（downgrade→裸 SQL 插旧行→upgrade→断言 upload） |

## 评审（两轴）与修复

硬违规 0、越刀 0。修复 2 项：**同一 open 缺口二次登记 409**（原「最后登记者赢」会让先登记资产发布时缺口永不关闭——真缺陷）；存量回填测试补齐。

## Deviations

1. 会话中途方向更新包（ADR 0031-0033 + slices 重排 + LLM 留位）由计划者产出，已单独成笔入库（98ded04）；0031 语义与实现逐句对账一致。
2. tab 顺序两源冲突（原型「待人洗/已接入」vs 工程「已接入/待人洗」）：保持工程现状（与三态流转序及详情页文案一致），记偏差。
3. 宿主 5432 被另一项目（suanming）占用，本仓 compose 栈当前未运行；路径验收与计数均在栈存活期间采集，修复轮经等效临时库验证。
4. 拒答缺口芯片仅流式 complete 时呈现（消息表不存 gap 关联，ADR 0030 取舍）；刷新会话后芯片不重现——记 debt。

## 债务

1. 缺口芯片刷新不重现（见上）；如需持久需消息-缺口关联或 GET 时回填。
2. 幂等是应用层 SELECT-then-INSERT，无部分唯一索引（单操作者假设，ADR 0030 已声明）。
3. 缺口 product_id 恒空（自由文本不猜商品）；挂商品靠补文档时表单预选。
4. AssetsListPage 中文标签兼作枚举与 `?status=` 值；gaps 双请求挂载即发——重构留后续。
5. register 失败可留对象存储孤儿字节（继承第 3 刀模式）。
6. alembic Config 加载触发 DeprecationWarning（No path_separator）——工具链版本问题，非阻断。

## 下一 Owner 注意

- **第 5 刀主题已锁：MCP 只读已发布**（ADR 0001/0020/0032：同进程 `/mcp/` Streamable HTTP + Bearer（.env）、检索/取已发布（可按版本）/登记/导出、无 publish、README 给 Cursor mcp.json）。检索索引直接复用（0017）。
- 更后面（独立刀，不锁序）：修订流+回滚（落地后缺口默认开修订关闭，0031）、厂商生成（0033，密钥只在 .env）、顾客通道（0021/0033）、素材/切片/考核。
- 本机环境：5432 被 suanming 项目占用时，本仓栈起不来——停它或改 compose 端口映射；gh 需 HTTPS_PROXY=7890。
