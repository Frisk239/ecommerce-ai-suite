# 工程第 39 刀两轴评审：自进化仪表（feat/evo-dashboard）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`1ad5521`+`5e9a62f`）。Spec：`.scratch/evo-dashboard/spec.md`（goal §6.2.6+路 B 升级项 1/2；0031 修订）。Owner 亲评。

**净，可合。** hit_count 快慢路径双累加；保鲜降权为乘数后处理不动打分公式，**null 不降权**裁决钉死 docstring（评测基线逐位一致实证 65.0/75.0 全不变）；resolve 验证闸在 publish 事务内切块+指针前移之后跑（新块同 Session 可见）；「补错文档保持 open」钉测。Owner 验收发现并确认非缺陷：表单字段名为 `knowledgeGapId`（camelCase 前端约定）——首轮 API 级验证用错字段名导致缺口未挂接，换正确字段名后全链通过。

P2 记债：缺口挂接后放弃出口仍无（resolved_by_asset_id 已挂但补文档被废弃/丢弃时缺口被锁——生命周期出口刀遗留）；stale 降权无端到端测试（单测覆盖 is_stale+乘数，无「回拨 91 天→检索排序变化」集成）。

## 计数（Owner 复跑）

- ruff：All checks passed；无 DB `652/491/0/161`；带 DB `652/652/0/0`（645→652 只增）
- **评测基线逐位一致**：overall 65.0/75.0（null 不降权实证）
- Owner API 级验收：热度（同问法两次拒答→hit_count=2 置顶）；verify（200+audit+时间刷新）；**验证闸全链**（错文档发布→open 保持；对文档发布→resolved）
