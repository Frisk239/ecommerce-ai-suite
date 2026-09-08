# 工程第 14 刀 closeout：库存工具（feat/stock-tool）

日期：2026-09-08。上刀 intake：`docs/progress/order-tools-intake.md`（通过）。短对齐：`.scratch/stock-tool/spec.md`；领域裁决：ADR 0037（0036 同模式第二实例）。基线=`origin/main`（20b9453，PR #17 合并后）。

## 交付（只读 get_stock 回答有没有货）

1. **迁移 0008**：`products.stock` 可空 int（NULL=未设置）；种子回填（钛钢保温杯 42、瓶装水 0），幂等且**仅 NULL 回填不覆盖手改值**。
2. **`services/stock_tools.py`**：词表分派（有货|没货|无货|缺货|库存|现货|剩，与 ADR 逐字一致）；`match_product` 纯函数（商品名与问题 LCS ≥2 字，多命中取最长、平手取 id 小——「保温杯有货吗」能命中「钛钢保温杯」）；`query_stock` 引擎单点入口；摘要（「有货 · 42 件」等）。
3. **引擎分派序**：订单号（第 13 刀）→ 库存关键词 → 检索。五分支回答：stock>0「有货，当前库存 N 件」/ ==0「暂时无货」（事实不是失败，kind=answer）/ NULL「库存未设置」转人工 / 商品未命中「没有找到对应商品」转人工 / DB 异常「库存查询失败」转人工——handoff 分支不检索、不调 LLM、不产生缺口（0018/0024）；citations 恒空；复用 `tool` 事件与工具条，thinking 按工具名分叉「查询库存中…」。
4. **web**：客服/顾客页建议问题加「钛钢保温杯有货吗？」；零新组件（ToolStrip/徽章复用）。
5. 词条闭环：_Avoid_「用规格文档回答有没有货」自此不发生——有货没货只有工具说；「保温杯的净含量」仍走检索引用零漂移（钉测试+浏览器实证）。

## Owner 验收（浏览器点穿，六项全过，顾客通道）

「钛钢保温杯有货吗？」→ 工具条 `get_stock(钛钢保温杯) → 有货 · 42 件` +「有货，当前库存 42 件。」无引用；「瓶装水有货吗」→「暂时无货。」+工具条；「小龙虾有货吗」→「已转人工」徽章+「没有找到对应商品」、无拒答徽章、无缺口芯片；「保温杯的净含量是多少？」→ 引用 `A-0009 · v1`（不走库存工具）；「我的订单 SO-1001 到哪了？」→ 订单工具照常（分派序无回归）。

## 两轴评审与处置（`docs/progress/stock-tool-review.md`）

- Standards 零硬违规可合；Spec 高度一致零越界（评审子代理真 PG 全量复跑绿）。
- 无代码修改；ADR/intake 文档由 Owner 随 align 提交。
- 记候看：`_run_stock_ask`/`_run_order_ask` 重复 45 行——**第三工具时收共享缝（审计刀 3 候看项）**；thinking 文案三元链；裸 dict 协议；「剩」字词表误伤面。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=301 passed=225 failed/errored=0 skipped=76`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=301 passed=301 failed/errored=0 skipped=0`（第 13 刀基线 259 → 301 只增）
- web：`npm run build` 通过

## 遗留

- 评审债务四条（轻）；「剩」单字词表的准规格误伤面待真实问句分布回看；库存写操作/预警/别名表/多仓/价格 Out。
- 无商品名的泛化库存问法（「都有什么货」）不在词表语义内，走检索路径（现状拒答转人工）。

## 下一刀

刀计数：本刀第 14。**第 15 刀为普通工程刀，其后触发审计刀 3**（计数线到）。第 15 刀候选：评测集（golden conversations + policy edges 进 CI，0027）或素材模块启动——按债务与演示缺口在开刀时裁决。CONTEXT 推进句已回写。
