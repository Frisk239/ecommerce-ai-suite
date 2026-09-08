# Closeout · 审计刀 1（六刀后全面审计）

- 日期：2026-09-08
- 基线：main `6806090`（六刀全并：脚手架/治理发布写回/客服引用/知识缺口闭环/MCP 只读已发布/修订流+回滚）
- 形态：三路只读子代理（设计符合性/技术债/功能缺口）+ Owner 全量门禁与 P0 亲证修复
- 分支：`feat/audit-1`（P0 修复 + 本 closeout）

## Owner 全量门禁（命令输出原文）

| 项 | 输出 |
| --- | --- |
| 栈 | `docker compose down -v` 重置后三容器 Up（db healthy），`/health` 200 connected |
| `uv run pytest` | `107 passed, 33 skipped, 1 warning` |
| 集成（SUITE_TEST_DATABASE_URL） | `140 passed, 18 warnings`（修复后 `141 passed`） |
| `uv run ruff check .` | `All checks passed!` |
| web | `✓ built in 12.00s` / `2 warnings, 0 errors`（既有） |
| MCP smoke（真端点） | connected/protocol 2025-11-25/四工具/空库统一口径拒绝/export 空数组——行为全对 |
| 前端 | 5173 200、proxy `/api/health` 200 |

## 三路审计结论

**设计符合性（33 ADR + 词表逐条）**：无硬偏离；11 组核心不变量全部确认（修订唯一=DB 部分唯一索引+应用预检；三检索面同一函数 join 指针；发布四步单事务；MCP fail-closed；0031 修订关缺口已落地等）。观察级 4 条（见 P1/P2）。

**技术债**：P0 唯一——**回滚写回残留**（`_write_back_product` merge 语义：v2 独有字段回滚后残留，指针与商品口径漂移）→ **已修**（ADR 0034 全量语义+残留场景测试，141 passed）。第六刀（修订流+回滚）代码质量细审通过（迁移无越界列、回滚独立事务留痕、继承语义正确）。

**功能缺口**：七块中三块可演示（客服接待飞轮/中台治理/MCP 连接层——六刀全部押在这三块且闭环）；四块零开工（运营/切片/素材/考核，ADR 已锁按计划排队）。顾客通道与厂商生成依赖就绪度高（引擎同源/凭证已备）。

## P1（进第七刀及后续施工单，未修）

1. 孤儿字节模式（登记/开修订 put_bytes 先于约束检查，并发失败留孤儿）——统一清理对策。
2. 无「放弃修订」动作：修订开错后占死唯一槽（缺口挂修订同源）。
3. 开修订/挂缺口无审计留痕（0016 只锁发布/回滚/确认三类——若要扩需 ADR）。
4. 缺口幂等无部分唯一索引（并发窗口）；拒答缺口芯片刷新不重现。
5. 对话证据句复读顾客原话（answer.py）——厂商生成刀顺带重做组装。
6. retrieve() 全量拉取 10000 上限；MCP register 无 2MB 前置；read_version_text 无上限。
7. 集成测试 module 级共享登录态顺序耦合；test_sessions 假断言；spec_schema 拷贝四处；前后端取值双实现。
8. 发布闸门不判 kind（新种类误闸风险——素材/图片刀前补）。

## P2（备注）

AssetsListPage 中文标签枚举/双请求；mcp 1.x 线（2.x 改名）；DNS-rebinding 仅 localhost（部署期）；compose web=dev server、cookie 无 Secure、生产构建 /api 无反代；缺口 product_id 恒空。

## 裁决：第七刀主题

审计后进入第七刀，**厂商生成**（ADR 0033：LLM_API_KEY/BASE_URL/MODEL 已备、零新表、接待闭环从模板变真回答，仍受 0018/0007 约束）。顾客通道（0021）排第八刀候选。运营/切片/素材/考核保持排队。

## 下一 Owner 注意

- 本机 5432 空闲期可直接跑集成（`SUITE_TEST_DATABASE_URL=postgresql://suite:suite@localhost:5432/suite_test`，conftest 自建库）；suanming 项目占用时用 `PG_PORT=5434` 起栈+5433 临时测试库。
- gh 需 `HTTPS_PROXY=7890`；LLM 凭证只在 `.env`（已备，勿入库）。
- 审计计数线：下一轮审计约第十一刀。
