# 工程第 40 刀两轴评审：忠实度、反馈与两阶段写（feat/fidelity-feedback）——收官刀

日期：2026-09-09。Fixed point：`origin/main...HEAD`（两笔）。Spec：`.scratch/fidelity-feedback/spec.md` + ADR 0044（四段语义）。Owner 亲评+API 全链验收。

**净，可合。** 两阶段语义与 ADR 0044 逐条一致：资格窗期（15 天）与 token HMAC 全在代码、create_return 不进模型注册表（越狱提议被拒钉测）、确认端点验签+资格重查+幂等 409；忠实度闸 coverage<0.4 且命中≤1 才触发（评测大集 runner 直调 retrieve 不经闸——逐位一致 65.0/75.0 设计即零影响）；feedback 端点幂等 409+白名单（仅 answer+citations 非空）+分诊置 null；顾客确认端点 401（0016 口径，spec 403 的合理修正）。实现方 35 测覆盖全链（含张冠李戴 token 400/越窗 409）。

P2 记债：忠实度闸无线上端到端实证（单测+评测设计证明，浏览器擦边句验证留 Owner 手册）；分诊在 last_verified 本就 null 的资产上无可见 diff（演示库多资产未验证——功能正确演示效果弱）；确认卡片 UI 最小实现（tool 文本检测渲染，非结构化卡片）。

## Owner API 全链验收

①资格步：问「SO-1001 我想退货」→ tool 记录 `check_return_eligibility → 可退货 · 待确认 token=35ec…` + 回答「等待客服确认」；②确认：POST confirm-return 200，events 追加「退货申请已确认（操作者确认两阶段）」；③进度可查：再问 → 轨迹含确认事件（15:22）；④幂等：重复确认 409；⑤feedback：顾客回答（citations=[A-0009·v1]）点没有帮助 → 200 → 资产置未验证 → 二次 409。（注：顾客会话 IP 限流 5/60s——测试脚本需换 XFF。）

## 计数（Owner 复跑）

- ruff：All checks passed；无 DB `687/516/0/171`；带 DB `687/687/0/0`（652→687 只增）
- 评测基线逐位一致：overall 65.0/75.0
