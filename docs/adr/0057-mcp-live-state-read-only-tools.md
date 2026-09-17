# ADR 0057：MCP 活状态只读工具（get_product / get_stock / get_order_status）

- 日期：2026-09-16（第 99 刀）
- 状态：接受
- 相关：ADR 0002（订单/库存/商品行不是中台对象）、0020（连接层只读已发布）、
  0021/0036/0037（顾客无账号、订单/库存是工具数据源）、0032（连接层 Bearer）、
  0038（出口必掩：字节不动、出口打码）、0043（bounded agent loop 与 TOOL_REGISTRY）、
  0045（商品行价是行的事实）；roadmap 第 99 节（grill 定案：三工具/复用函数/断言
  恰七）；词条「连接层」

## 背景

连接层此前只有知识四件（search/get/register/export）。外部 Agent（Cursor 等）
接进来后问「这单到哪了」「还有货吗」「这杯子多少钱」，连接层只能答「去站内
查」——可这些查询在站内客服早就有：agent loop 的 TOOL_REGISTRY 里挂着
get_order_status/get_stock（0043），目录回落挂着商品匹配与类目聚合（0045）。
原口径「活状态不进连接层」（0021/0036）的本意是**活状态不进检索索引**——
订单/库存不是中台资产、不该被当成知识切块检索；它从未要求「外部不能查行」。
第 38 刀的 E3 反向断言（工具名不得含 order/stock）把这个本意钉过了头。

## 决定

### 1. 三件活状态只读工具进连接层，恰七且仍无 publish

- `get_order_status(order_no)`、`get_stock(product_name)`、`get_product(name)`
  注册进 mcp_server 的 TOOL_REGISTRY 同层（连接层共七件：知识四 + 活状态三）。
- 协议证据断言随之改口径：E1「恰四」→「恰七、仍无 publish」；E3「无活状态
  工具」→「活状态工具**只读**」——三件在列，且工具名/描述/schema 不含
  publish/create/update/delete 写动作字样（pytest test_mcp_evidence + smoke
  --evidence 同步）。
- **不加任何写工具**（下单/改价/改库存/退货创建全不在连接层），发布权仍只在
  治理台（0005/0020）；退货创建走治理台确认端点携 HMAC 令牌（0044），不经 MCP。

### 2. 复用客服同一套函数——一致性由复用保证，不是对齐维护

- `get_order_status`/`get_stock` 的执行入口就是客服 agent loop 的
  `services/agent_tools.TOOL_REGISTRY` 同名条目：同一份 `_validate_args` 白名单
  校验（参数键白名单、order_no `SO-\d+` 格式与大写归一）加同一个执行函数
  （`_run_get_order_status`/`_run_get_stock`——含 get_stock 的先类目后单品、
  部分名 LCS 容错与纯度闸）。连接层不自建第二套校验或查询。
- `get_product` 复用客服目录同款匹配：`stock_tools.match_product`（LCS≥2 部分
  名）与 `catalog_tools.category_targets`（类目名+口语别名，先类目后单品）。
  返回单品 `{found, id, name, category, price_cents, currency, stock,
  spec_values 摘要}`；类目聚合 `{found, category, total, in_stock, priced,
  价格区间}`（混币种不出区间，与 0045 同口径）；未匹配 `{found: False}`
  （与 get_order_status 查无同形）。
- 商品/库存/订单仍是工具数据源不是中台对象（0002）：三件读的是商品行/订单行
  的活状态，不经检索索引、不进治理台。

### 3. 脱敏边界：出口剥联系方式，字节不动

- 当前订单 mock 无 PII（seed 只有商品名与轨迹文案）；但 items/events 是 JSONB
  自由形状，真实部署里快递/客服回执常把顾客电话/邮箱塞进事件文本或条目键。
- MCP 出口（`mcp_server._egress_order_result`，连接层出口专用，站内路径不走）：
  顶层与 items/events 条目**剥除**联系方式键（phone/tel/mobile/email/contact
  等小写匹配）；剩余自由文本（事件轨迹/商品名）过 `redact`（0038 出口必掩：
  手机号留前 1 后 2、邮箱掩 local——幂等，净值原样通过）。存储字节不动。
- `get_product` 的 spec_values 摘要（`{字段: 值}`，丢治理元数据）同样出口过
  redact——写回值可能混人工填的联系方式（ops 路径同纪律）。
- 钉测：tests/test_mcp_live_tools.py 补一条带电话的订单（事件文本电话+联系键），
  断言裸号码全文不出现、联系键被剥、文本呈打码形态。

### 4. 真实部署升级路径（本刀不做，记路）

- 现在的凭证模型与站内客服同：**单号即凭证**（0021 顾客无账号，单号由提问者
  自己给出）。mock 无 PII、出口已剥联系方式，演示态风险可接受；真实部署里
  单号可被枚举/转发，仅凭单号把订单轨迹（含收货城市级轨迹）交给任意持
  Bearer 的外部 Agent 不够。
- 升级路径=**单号 + 手机尾数双因子核验**：get_order_status 增可选参数
  `phone_tail`（如尾 4 位），与订单留存的顾客手机尾位比对，不符即
  `{found: False}`（不区分「单不存在」与「核验不过」，同 get_asset 对未发布
  的统一口径语义）。需要订单表补顾客联系位列（脱敏出口照旧剥除）。这是
  鉴权升级，属连接层凭证模型变更，须另开 ADR。

## 后果

- 外部 Agent 七工具全列表可演示「读同一权威」：知识与活状态一套连接层；写
  权威（发布/退货确认/商品改价）仍零暴露。
- test_mcp.py / test_mcp_evidence.py / mcp_smoke.py 的恰四断言全部改恰七；
  97 刀「SFT 导出不进 MCP」的注释口径同步为恰七。
- Connect 页四工具卡暂未同步为七（演示页文案，随后续 UI 刀跟进，不阻塞本刀）。
