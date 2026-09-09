# 工程第 28 刀 closeout：生命周期出口刀（feat/lifecycle-exits）

日期：2026-09-09。上刀：第 27 刀演示收官（goal 7/7）。本刀=**优化计划（docs/optimization-plan.md）第一刀**：P1 生命周期出口族三件一揽子。领域裁决：ADR 0042。基线=`origin/main`（c16c227）。

## 交付（负向生命周期管理补全——走查实证的三个死胡同全部打通）

1. **修订换字节**：`PUT /api/assets/{id}/versions/{no}/bytes`（multipart）——待人洗未发布版可用（线上版 409）；新对象键写、旧键字节删（**storage.delete 首次接线**，孤儿清理）；机洗重跑（extracted 重算、confirmed 保留）；机洗重跑不持事务（P1#2 纪律）。
2. **放弃修订**：`POST /api/assets/{id}/revisions/discard`——删未发布修订版（字节+行）、解锁回滚与再修订；audit `discard_revision`；v1 新资产 409 引导走废弃。
3. **废弃失败资产**：`POST /api/assets/{id}/discard`——仅「ingested 且从未发布」；`discarded_at` 标记（迁移 0014）+清全部字节+audit `discard_asset`；列表默认过滤；已发布资产 409。
4. **web**：详情页三按钮按状态显隐+二次确认（明示字节删除不可恢复）；「上传新正文」走文件选择；留痕面板新动作标签中文化。
5. **CONTEXT 词条**：「资产状态」补「废弃是治理动作后的隐藏标记，不是第四态」（Edit 精确替换）。

## Owner 验收（浏览器+API 全过）

换字节：开修订→上传新正文（「15天无理由」版）→确认→机洗重算（材质/净含量重抽+confirmed 继承）→API 发布→指针前移、v3 text=新正文。放弃修订：确认对话框→audit `discard_revision` 行+版本行删除+回滚解锁。废弃：A-0018（GBK 失败资产）200+列表消失；已发布 A-0013 409 兜底。

## 两轴评审（`docs/progress/lifecycle-exits-review.md`）

净可合（三闸门与 0042 逐条一致/纪律钉明/测试零删纯增）；P2×2 记债（delete-commit 窗口/计数口径）。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=533 passed=394 failed/errored=0 skipped=139`
- 带 DB（5433）：`tests=533 passed=533 failed/errored=0 skipped=0`（基线 510 → 533 只增）
- web：`npm run build` 通过

## 遗留

P2×2（评审）；优化计划下一刀：**第 29 刀客服多轮记忆刀**（goal ①「必须有：多轮」）；第 30 刀治理体验小刀；审计刀 6 于第 30 刀后。CONTEXT 推进句已回写。
