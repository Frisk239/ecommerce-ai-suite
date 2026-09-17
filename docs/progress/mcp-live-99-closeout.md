# 第 99 刀 closeout：MCP 活状态只读工具（第三梯队产品刀收官）

日期：2026-09-17。分支 `feat/mcp-live-99`（stacked 于 `feat/video-compose-98b`）。

## 交付

1. **三活状态工具**：`get_product`（match_product+category_targets 复用，返回价格/库存/spec_values 摘要）、`get_stock`/`get_order_status`（**执行入口=客服 TOOL_REGISTRY 同一条目**——「与客服读同一套函数」代码级成立）；工具恰七、仍无 publish。
2. **脱敏**：`_egress_order_result` 剥联系键+redact；测试补带电话订单钉剥除打码（承重钉）。
3. **断言更新**：E1 恰七、E3 改「活状态只读」口径（写动作字样断言只判活状态三件——知识四件 docstring 的历史 action 字面值豁免注释）；smoke 三件实测。
4. **ADR 0057**（复用一致性/无写无发布/真实部署双因子升级路径）；README 工具表四→七+CONTEXT 词条句；「恰四」活注释全清。

## 证据

- Owner 门禁亲验 **1461/0/0/0**（+5）+ ruff；**smoke --evidence E0–E3 全 PASS**（恰七无 publish/活状态只读无写动作字样）。
- 真栈实录：`get_stock("显示器")`→类目聚合 22/21/1085；`get_order_status("SO-1001")`→退货中+3 事件+**无联系字段**；`get_product("保温杯")`→129 元/库存 42/spec_values 带写回溯源。

## 评审实修（两轴子代理：无 P0/无 Standards 违规）

- 无需实修。确证：spec_values 值来源=已发布版本+出口 redact（泄密疑虑解除）；复用主张代码级成立（注册表同源直调）。

## 记债

1. **get_product 单品路径无纯度闸**：「手机壳」LCS≥2 可误命中「智能手机」返回商品档案（返回档案非答案，低危）——后续补 stock_product_residual 同款闸（101 刀样本池/审计 19 清单）。
2. Connect 页仍显四工具卡（Must 未列 UI；ADR 0057 已记）——间隙小刀或 103 前顺手。

## 后续

审计刀 19（覆盖第 93–99 七刀）→ 第 100 刀 OFF 订正 → 101 评测扩容+embedding 门 → 102 judge 观察 → 103 压测。**第三梯队产品刀（97/98/98b/99）收官——goal §4⑦「活状态只读工具」兑现，8+1 戏台全部落地。**
