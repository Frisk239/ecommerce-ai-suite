# 审计刀 5 closeout：三路并行审计（feat/audit-5）

日期：2026-09-08。范围：审计刀 4（24998d3）后五刀（PR #27..#31：打码出口收口/运营 Agent/总览连接页/后端债池/前端收口）+ 全仓现状。三路子代理 + Owner 亲跑门禁（ruff 过；无 DB `491/368/0/123`；带 DB `491/491/0/0`）。**不改产品代码**。

## 总判定

**三路一致 P0 零**。0041 逐条对账通过（投放零 Asset 写/refs 冻结/mcp 行不可登录/非中台对象）；ADR/迁移链完整；CONTEXT 无损伤。audit-4 P1 清账：GIN/题库单资产/material 四小条/前端四项**全还**；N+1 部分还（22 刀回写一例 ops 列表）。

## P1 簇（收官排期输入）

**设计路 P1×3（打码收口漏网——21 刀宣称与实际有差）**：① material 生成 prompt 裸出进程（孪生 ops 路径掩了 material 漏了，`material.py:61-75`）；② MCP 响应 title 三处裸值（切片 `transcript[:60]`/素材生成 title 非净源，`mcp_server.py:170/207/280`）；③ ops_runs 落库未掩（spec_selling_points/detail/body 原值落非中台表，`ops.py:135-161`）。

**技术债 P1×4**：④ ops 列表 N+1 回归（`routes/ops.py:107-117`）；⑤ ruff format 基线劣化（44 文件未格式化，13 个为 21–25 刀新文件）；⑥ ops retry/deliver 无行锁（并发双跑竞态）；⑦ mcp 首插无 IntegrityError 兜底。

**缺口路（goal 完成标准 6/7）**：⑧ **README 3 分钟口述稿是唯一未达完成标准项**；血缘「导出」环有留痕未拼视图（30 行前端）；`knowledge_gaps.question` 出口未掩；总览「其余能力」行；拒答交接摘要固定文案。

## P2 池（记录）

检索索引行落库原文（读侧已掩——ADR 未括注豁免口径）；via 标签「检索」口径不实（ops 直查指针不走索引）；audit action 注释三值；CONTEXT 无「编排任务」词条（撞「任务」词条表面）；顾客令牌 TTL；孤儿字节（delete 零调用方）；放弃修订死锁（产品决策级）；检索无缓存/万行全扫；列表无分页；GIN 无 EXPLAIN 钉；残存 running 无启动清扫；slices.md 结构脏（空标题+已交付刀重复在册）；CI/反代/备份/监控（部署面）。

## 收官排期（Owner 裁决）

| 刀 | 内容 |
| --- | --- |
| **第 26 刀：语义收口小刀** | P1①②③（打码漏网三处——material prompt/MCP title 三处/ops_runs 落库掩）+④⑦顺手（ops N+1/mcp 首插兜底）+⑥（ops 行锁或单操作者裁决句）+缺口路三小件（血缘导出块拼装+export 中文化+gaps.question 掩）+slices 结构清理 |
| **第 27 刀：演示收官刀** | README 3 分钟口述稿（goal 最后一条完成标准）+总览「其余能力」行+拒答交接摘要（问句+缺口芯片）+ruff format 基线收口（纯格式 commit）——**达成即 goal 完成标准 7/7** |
| 第 28 刀起 | 部署阶段：CI workflow+反代/HTTPS+备份+监控；真视频/ASR（0039 部署语境）单列 |
| **审计刀 6** | 第 30 刀后触发 |
