# 第 80 刀 intake（对第 79 刀 rerank）+ 对齐

日期：2026-09-11。

## 对第 79 刀的 intake

| 项 | 结果 |
| --- | --- |
| Merge | `feat/rerank-79` 已进 main（PR #114，HEAD=b69a1cd） |
| Evidence | 集成 1166 passed / **0 skipped**（SUITE_TEST_DATABASE_URL 显式携带 + junitxml 机械计数）；ruff 绿；评测 after 87.5/86.2 与实验复算逐位一致（Spec 轴评审独立复核）；live 两病例（A-0009/A-0003）Spec 轴直调 retrieve 复核一致 |
| Spec vs claim | 评审两轴证伪后全实修（P0 测试隔离 / P1 伪钉反转、中性措辞降级、计数订正）；alpha=0 证伪验证两钉必红 |
| Safety | 无密钥入库；data/tmp 临时件已清 |

**通过**（评审实修后）。债：无新增（退化标题满额乘数、stale×亲和优先级已作为 ADR 0048 取舍披露，属校准位非缺陷）。

## 本刀对齐（Owner 已裁决：允许给顾客加结束会话选项）

**用户路径**：顾客在 /customer 或嵌入 widget 里点「结束会话」→ 会话落终态（status=ended + closed_at）→ 顾客页显示「会话已结束」横幅、输入锁定、评分条仍可用（48 刀 CSAT 回退点兑现：结束态可评分）→ 点「开始新会话」重开。操作者在客服页看到「已结束」徽章；已结束会话仍可回流登记。

**状态机**：active →（顾客结束）→ ended（closed_at 落值，幂等）；active →（操作者回流）→ registered（现状不变）；**ended → registered 放行**（顾客结束的对话内容仍是可回流的知识素材）。ended 不可逆回 active。

**闸语义裁决**：「结束会话=关闭对话流（发问），不关善后通道」——ended 下：发问 409（对话终结）；评分/反馈/联系方式**放行**（registered 仍全 409，现状不变）。

**Must**：
1. `POST /api/customer/sessions/{id}/end`（幂等；闸序同 rating；ended→200、registered→409、active→迁移+closed_at）
2. models.py closed_at/status 注释更新（closed_at=会话终结时刻：顾客结束或操作者回流）；**无迁移**（status 是 String(20) 无 CHECK）
3. 顾客页：结束按钮（streaming 时禁用；embed 面同组件自动生效）+ 「会话已结束」横幅 + composer 锁 + 「开始新会话」；评分条保持可见
4. 客服页：SessionStatusBadge ended 分支 + labels + types union
5. 测试：end 端点全形态 + 评分/发问/回流的 ended 行为 + registered 不变钉子

**Out**：自动超时结束、操作者侧结束、stats 完结率口径、closed_at 拆列（顾客结束 vs 回流两个时刻）、结束触发 CSAT 弹窗（评分条本就常驻）。
