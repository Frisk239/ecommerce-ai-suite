# 第 45a 刀规格：顾客令牌 TTL（intake + Owner 裁决）

日期：2026-09-10。分支 `feat/token-ttl`（基于 main `c8f53eb`）。依据：`docs/roadmap-product-hardening.md` 第 45 刀（第二梯队第一项）的**安全面那一半**。**第 45 刀拆两刀走**：45a = 令牌 TTL（本刀，有越权/过期钉测要求）；45b = 嵌入 widget（loader + /widget + origin 白名单，另开）。

## 痛点

顾客会话令牌**永不过期**：`POST /api/customer/sessions` 签发的 `secrets.token_urlsafe(32)` 存进 `service_sessions.customer_token` 就一直在。签发处的 docstring 自己写着「不做过期/刷新/吊销（本刀 Out）」——从审计刀 2 起就在案。一个泄露的令牌只要会话还 active，永久可发问（等于永久可写会话内容）。

## 现状（实测）

- 令牌签发：`routes/customer.py:149-150`（无过期字段）。
- **令牌校验在三处逐字重复**：发问（`:180-186`）、反馈（`:252-258`）、提交联系方式（`:342-348`），都是 `session is None or token is None or customer_token is None or not compare_digest(...)` → 统一 401。
- `settings.session_ttl_seconds = 7*24*3600` 是**操作者 cookie** 的 TTL，与顾客令牌无关。

## 裁决

| # | 裁决 | 理由 |
| --- | --- | --- |
| 1 | TTL = **24 小时**，新设置 `customer_token_ttl_seconds`（可被 env 覆盖） | 路线图点名 24h；独立设置项，不与操作者 cookie 的 7 天混用 |
| 2 | 落成新列 `service_sessions.customer_token_expires_at`（迁移 **0021**），**迁移内回填** `created_at + 24h` | 老行不编造「永不过期」，也不留 NULL 逃逸口；存量会话按签发时间 + 24h 判定（演示库的老会话因此过期——顾客页每次新建会话，无历史依赖，安全） |
| 3 | 校验**抽成单一出处** `_authorize_customer_session`（存在 + 恒定时间比对 + 未过期），三处调用点改用它 | 现有三处逐字重复；TTL 若再抄第四份迟早漂移。顺带消掉三份重复鉴权 |
| 4 | **过期与无效同 401、同文案**（不区分「令牌错」与「令牌过期」） | 对攻击者不泄露「令牌曾经有效」这一信息；也是既有口径（会话不存在与令牌无效本来就统一 401） |
| 5 | `expires_at is None` 视为**不可用**（严格），不视为「永不过期」 | 迁移已回填全部存量行，NULL 不该出现；严格判定让它一旦出现就是显式失败而不是静默放行 |
| 6 | 本刀**不动**「刷新/吊销」语义 | 路线图只要求 TTL；会话终结（回流登记置 registered）已使令牌失去发问资格（既有 409），续期/吊销另议 |

## 验收（路线图要求「安全面变更必有越权/过期钉测」）

1. 新建会话：`customer_token_expires_at ≈ now + TTL`（± 容差）。
2. 令牌有效 → 可发问（控制组）。
3. **过期 → 401**：把该会话的 `expires_at` 改成过去 → 发问 401。
4. **同文案**：过期 401 的 detail 与「令牌无效」401 的 detail **逐字相同**（钉住裁决 4）。
5. 三处校验点都受约束：发问 / 反馈 / 提交联系方式（至少覆盖发问与反馈）。
6. `expires_at = NULL` → 401（裁决 5）。
7. 越权面：**别人的令牌**拿不到本会话（A 会话的令牌打 B 会话）仍 401（既有契约，回归钉住）。

## Out

- 令牌刷新/续期/吊销、嵌入 widget（45b）、顾客端「会话已过期」的专门 UI（本刀只保证 401 与既有错误呈现一致）。
