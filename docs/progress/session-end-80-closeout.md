# 第 80 刀 closeout：顾客侧结束会话 + 会话终态语义

日期：2026-09-11。`feat/session-end-80`。**无新表无迁移**（status 是 String(20) 无 CHECK，closed_at 复用）。

## 交付

| 件 | 交付 |
| --- | --- |
| 状态机 | `active → ended`（顾客点「结束会话」，幂等、closed_at 落 DB 钟）；`active → registered`（现状）；`ended → registered` 放行（顾客结束的对话仍是可回流知识素材，closed_at 保持顾客结束时刻）；ended 不可逆 |
| 后端 | `POST /api/customer/sessions/{id}/end`（闸序同 rating：IP 闸→401→状态闸；幂等；registered 409）；闸语义「结束=关对话流不关善后」——ended 下发问 409（`_status_label` 中文映射）、评分/反馈/联系方式放行、registered 全部照旧 409 |
| 并发收口（评审 P1 实修） | end 与回流收口都改**原子条件更新**（`UPDATE … WHERE status='active'` / `WHERE status IN (active,ended) AND registered_asset_id IS NULL` + `COALESCE(closed_at, now())`）——评审探针实测旧读-判-写形态在 register_asset 的机洗 commit 窗口（可达 20s）下会被并发写穿透（closed_at 被重写/ended 半态）；回流路径的 Python 钟一并收口为 `func.now()` |
| 前端 | 顾客页顶栏「结束会话」次级按钮（streaming/过期态禁用或隐藏）+「会话已结束」横幅（同过期 banner 形态、中性配色）+ composer 锁 + 评分条保留 + 「开始新会话」恢复（含评分态完整重置）；客服页 `SessionStatusBadge` 「已结束」分支 + labels/types 同步 + ended 横幅按 canRegister 分支 + 按钮文案 ended 下切「回流登记」；embed 面同组件自动生效 |

## 验收

- 集成 **1174 passed / 0 skipped**（1166→1174，+8；`SUITE_TEST_DATABASE_URL` 显式携带 + junitxml 机械计数）；ruff 绿；前端 lint 7/0（基线一致）、build 绿。
- 浏览器（真容器）顾客页全路径：建议问句 → 回答正确（A-0009 引用，79 刀 rerank 顾客通道可见）→ 点「结束会话」→ 横幅「会话已结束。感谢咨询，欢迎评分反馈。」+ composer 锁（placeholder 提示）+ 评分条保留 → **结束后打 5 星**「已提交 5 星，可随时修改」（48 刀 CSAT 回退点兑现）→ 「开始新会话」全部恢复。
- 客服页：会话 #241 显示「已结束」徽章 + ★ 5 + 「结束并回流登记」按钮可用 + ended 提示文案。
- 冒烟（rebuild 后）：幂等两次响应逐字相同（closed_at 微秒级一致）；ended 下评分 200、发问 409（「当前状态: 已结束」）。

## 两轴评审（全实修）

- **Standards P1**：并发 TOCTOU（上表并发收口行）——42 刀 SAVEPOINT、71 刀 UPSERT 的并发纪律本刀初次漏沿用，条件更新收口。
- P2×10：评分/反馈/联系方式三处 409 文案（裸 registered + 「只有进行中」假话 → `_status_label`）；回流 409 文案与新行为矛盾；docstring 五处「非 active」订正；顾客页结束按钮未挡过期态；客服页 ended 空会话引导指向隐藏按钮 + 按钮文案失真；测试辅助注释诚实化；README 路由清单补 end；CONTEXT.md 会话词条三态；回流路径时钟口径统一 DB 钟。
- Spec 轴：无 P0/P1（真容器实测幂等/闸序/回流 closed_at 逐微秒一致、Out 清单无偷做、ended 无逆向路径、前端无漏处理 ended 的消费点）。

## 诚实披露

- 「结束+评分+新建会话」三连发中 end 吃 IP 发问闸配额（与 rating 同款，intake 明示裁决）；会话级操作有 Bearer，认为可接受。
- register 收口条件更新失败（窗口内并发回流）时资产已建但会话不指向它（409 如实报）——**并发双回流产生两份资产**是本刀之前的既有形态（无 session 唯一约束），未在本刀扩修，记观察位。
- 评审轴真容器实测自建会话 243 并回流为资产 270；本 Owner 浏览器验收留会话 #241（已结束 · ★5，正好是「结束→评分」闭环的演示素材）。slices.md 第 48 刀历史小节里「本仓顾客通道没有顾客关闭会话事件」是历史记录，不篡改（48 刀当时属实）；活文档（README/CONTEXT）已更新。

## 后续

- **下一刀=审计刀 16**（覆盖第 76–80 刀；三轴并行只读 + P0/P1 实修）。
- 观察位：并发双回流两份资产（既有）、顾客会话历史回放（README v1 边界外）、ended 会话的自动超时归档（无）。
