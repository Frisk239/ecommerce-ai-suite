# 第 84 刀 intake + 对齐（结转记债清偿，Owner 定序）

Owner：部署面暂缓；按上轮商定打包三件小而实的债（`_can_publish` 废弃面 / 会话生命周期指标 / OOV 点名文案）。每 ask 三笔全库查询的合并留单独一刀（涉及检索主路径性能重构，需评测前后各跑一次）。

## Must
1. **`_can_publish` 废弃闸**（83 刀评审记债）：已废弃资产不可再发布——否则「废弃=隐藏」（0042）与「可发新版」冲突（新版在检索/导出面恒不可见=治理黑洞）；
2. **`service_session_transitions_total{from,to}`**（审计刀 16 B 轴记债）：建会话/顾客结束/操作者回流三处埋点，值域有界（new/active/ended/registered），不带 session_id；
3. **OOV 点名文案**（82 刀记债）：拒答首行点名未收录实体（「资料里没有与「戴森吸尘器」相关的信息」），非 OOV 路径保持 REFUSAL_CONTENT 原样。

## Out
- 每 ask 三笔全库查询合并（单独一刀）；`ops.fetch_published_refs` 废弃过滤（低险，留记债）。
