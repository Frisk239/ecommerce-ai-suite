# 工程第 39 刀 closeout：自进化仪表（feat/evo-dashboard）——goal §6.2.6 升格「已达成」

日期：2026-09-09。上刀：第 38 刀 MCP 协议证据。依据：goal §6.2.6+roadmap 第 39 刀行。基线=`origin/main`（e15a027，PR #47）。

## 交付（缺口打分排序/条目保鲜/0031 修订验证闸）

1. **缺口热度**（迁移 0016 hit_count）：归一化命中 open 缺口→累加不新建（快/慢路径双覆盖）；列表按热度降序；UI「被问 N 次」徽章 N>1 高亮——Intercom Fin 式缺口优先级的最小版。
2. **保鲜元数据**（迁移 0016 last_verified_at）：发布即置；「重新验证」动作（audit verify，Guru 验证语义最小版）；检索侧 stale 降权（显式验证后超 STALE_DAYS=90 天 score×0.5，**null 不降权**保守裁决——评测基线逐位一致实证）。
3. **0031 修订：resolve 前检索验证闸**：发布事务内对缺口 normalized_question 调 retrieve（切块+指针前移后，新块同 Session 可见）——**命中才 resolved**；「补错文档」发布→缺口保持 open 可再挂——闭环自愈语义升级（审计 A 路「错配自愈」从被动再排队升为发布即验证）。
4. web 三件（热度列/重新验证按钮+最近验证行/未验证>90天徽章）；CONTEXT 缺口词条补热度句。

## Owner 验收（API 级全链）

热度：「羊绒围巾起球怎么处理」拒答两次（差问号）→hit_count=2 置顶；verify A-0003 200+audit；**验证闸**：错文档（仓库存储说明）挂缺口 8 发布→**仍 open**；对文档（去球器处理）发布→**resolved**（首轮字段名 knowledgeGapId 用错未挂接——确认前端约定后全链过）。

## 计数

- ruff 全过；无 DB `652/491/0/161`；带 DB `652/652/0/0`（645→652）；评测基线 65.0/75.0 逐位不变

## 遗留

P2×2（缺口挂接放弃出口/stale 无端到端集成）；类型标签四分类/周报（观察项）。

## 下一刀

**第 40 刀忠实度、反馈与两阶段写**（忠实度闸/逐句引用/thumbs-down 分诊/check_return_eligibility→人确认→mock create_return——**写工具新 ADR**，roadmap 收官刀）→ **审计刀 7**（第 40 刀后）。CONTEXT 推进句已回写。
