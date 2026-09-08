# 审计刀 2 closeout：三路审计（feat/audit-2）

日期：2026-09-08。对账范围：`7e41a3d..origin/main`（审计刀 1 基线后五刀：第 7 刀厂商生成 / 第 8 刀顾客通道 / 视觉收尾 / 第 9 刀 CSV 导入 / 第 10 刀安全面收口）。基线门禁复现：无 DB `147 passed, 52 skipped`；带 DB `199 passed`；ruff 全过；web build ✓ / lint 0 errors。

## 三路结论

**路 1 领域对账：零硬偏离。** 七项逐条对上：0021 顾客端点仅签发+发问（无发布/回流/列表路径，chat_engine 仅差 gap_id 载荷白名单）；0025 CSV=upload 端点定值无偷渡；0023/0017 两通道真同引擎（代码级分叉仅 expose_gap_id 一参），MCP 仍是同一 retrieve；0007/0018 citations 恒服务端定+无证据不调模型（测试钉死）；0030/0034/0013 全部成立。

**路 2 安全与密钥：无 P0。** 密钥纪律全链通过（.env 从未入库全史验证、日志零凭证、异常链不进响应、conftest 无旁路、镜像不落层）；令牌 256bit 熵+恒定时间比较；LLM 注入面通过（前端纯 React 文本插值，零 innerHTML）；前端零 storage 残留。

**路 3 工程质量：无新 P0。** SSE 生成器不碰 DB、file.filename 死参、无商品资产闸门空集正确放行、写回 product=None no-op——四项嫌疑逐一排除。

## P1 簇（唯一主题：「单操作者无并发」前提失效，0016/0023 的取舍基座在第 8 刀公开面后未重估）

1. **LLM 长活持 DB 连接**：`run_ask` 检索开事务后 `await llm.stream_chat`（至多 20s）idle-in-transaction；引擎池默认 5+10 → 16 并发顾客即池尽（chat_engine.py:98→133、db.py:39）。
2. **SSE 慢连接钉池**：无响应超时 + `get_db` 到响应体完才归还 → 慢读连接可持续占连接（DB 层 DoS 面；与 1 同根：池容量 × 持有时长）。
3. **import_csv 冻结事件循环**：async 路由内 200 行同步 put_bytes+机洗+commit → 导入期间伴流 SSE 全卡（assets.py:279-295）。
4. **缺口幂等仅应用层**：check-then-insert 无部分唯一索引，并发同问拒答重复落缺口（audit-1 已记，顾客并发面使其成为现实问题；knowledge_gaps.py:29-38）。
5. **login 无限流**：默认弱口令 operator123 + dev session_secret 可在线爆破（存量，auth.py:41）。
6. **limiter `_events` 字典无界**：key 只增不减；trust 模式伪造首跳=可攻击内存缓涨（rate_limit.py:40）。
7. **retrieve `limit(10000)` 无 order_by**：超万行候选集非确定静默截断（audit-1「retrieve 全量」的新证据；retrieval.py:217）。

## P2（记录）

令牌明文列存储无吊销/过期（威胁模型边界已在 spec 声明）；compose HTTP 明文部署；XFF trust 误配风险（部署责任已写 README）；闸后 409/422 失败仍耗会话配额；`createCustomerSession` 走 request() 恒 credentials:include（签发端点无提权面，与 streamSse omit 口径不一）；web `.dockerignore` 未排 .env（当前无 web 级 env 文件）；0030 gap_id 通道分叉未回写 ADR；seed 枚举保留未用（CONTEXT 词条预告差）；CSV 孤儿字节放大（storage 无 delete API）；测试脆弱点：customer module IP 建会话闸余量 1、全局计数断言与 `list[0]` 序依赖、无 CI workflow（无库环境静默全 skip 假绿）。

## 债务核实三列（路 3 摘要）

**已还**：回滚写回残留（0034）、XFF fail-closed 两模式、401 存在性收敛、CSV 2MB 前置、client_ip 口径钉测。
**部分**：缺口幂等（应用层已做/索引欠）、证据句复读（字段块已修/对话块无角色过滤）。
**仍欠**：孤儿字节、放弃修订、开修订留痕、retrieve 全量、测试顺序耦合、发布闸门不判 kind、TTFT 先收全、模型输出无证据校验、UiMessage live 构造两处、askService/askCustomer 双闭包、RegisterAssetDrawer 重构批、限流测试样板、ServicePage 空态不居中。

## 裁决

- **P0 零**：五刀无阻断级缺陷，不触发审计刀内的产品代码修复。
- **文档笔随刀**：ADR 0030 补 gap_id 通道载荷分叉的实施澄清（顾客白名单，操作者保持）；CONTEXT「来源」词条不动（seed 枚举预告差记录在案即可）。
- **下一刀 = 并发收口刀（P1 簇一揽子）**：优先于回流增强——后者加重 LLM 调用面，须先修地基。范围：引擎池参数与 run_ask 事务边界（LLM 等待期不持 DB 事务）、import_csv 挪线程池（run_in_executor 或 sync 路由）、SSE 响应超时、缺口部分唯一索引、login 限流、limiter 过期 key 清扫、retrieve 排序定截断。其中表结构变化（缺口唯一索引）映射 ADR 0024/0031。
