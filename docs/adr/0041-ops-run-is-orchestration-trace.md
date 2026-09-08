# 运营 Agent：编排轨迹是模块自有数据，投放是渠道动作不是治理发布

状态：第 22 刀短对齐产出（Owner 裁决，audit-4 排期：能力 7/7 最后一块）。映射 0012（run 不是中台对象）+ 0007（output.refs 冻结当时版本）+ 0004（只读已发布做引用）+ 词条「发布」_Avoid_「投放发布（运营把素材发到渠道，那是另一件事）」，无新中台对象、无新来源种类。

## 语义

- **`ops_runs` 表（迁移 0012）非中台对象**：product_id / steps JSONB（三步轨迹：key/name/status(pending|running|done|failed)/via/detail）/ output JSONB（title/body/refs=[{asset_id,version_no}]）/ delivered_at / created_at。不能被检索、不能发布、不进治理台。
- **三步同步就地执行（0012 无队列先例），轨迹可观察**：
  1. `read_product`（via 中台·商品）——读商品与规格字段；
  2. `gen_material`（via 厂商模型）——`complete_chat` 直接生成投放文案**草稿**（不落素材任务、不落资产——运营中间产物只在 run 行内；要入库走素材中心人工路径）；**空 key/LLM 失败=该步 failed 可重试**（真实失败面，同素材生成无降级纪律）；
  3. `compose`（via 中台·检索）——检索该商品**当前已发布**的素材/切片资产做引用（refs 冻结当时版本号，0007 演示诚实度先例）；无可用引用时正文由商品规格卖点兜底并在引用区明说（原型口径）。
- **重试语义**：failed 步一键重试，从失败步续跑（前序 done 不重跑）；重置=新 run。
- **投放（DELIVER）=渠道动作**：操作者确认（记确认时间），**不改任何资产三态**（词条「发布」_Avoid_ 钉死）；已投放的 run 不可再投放。
- **MCP export 留痕（顺手，血缘「导出」环垫底）**：`export_published` 调用时写一行 audit_log（action="export"，含资产版本与调用方），血缘 writebacks 查询的 action 集合不含 export——不混入；后续血缘「导出」块从此有料可拼。

## 动机

goal 七块能力唯一未启动块（「可观察的工具调用轨迹、失败重试、人工确认后再发布」+死亡三问「编排可见、失败可重试」）；原型 Ops.tsx 交互状态机已冻结。

## 后果

- 新页「运营 Agent」+侧栏入口；种子商品即输入（无需种子 run）。
- Out：真渠道对接（投放 mock）、多商品批量 run、定时投放、run 历史 dive、投放效果回流、运营引用素材切片之外的中台对象。
