# 第 95 刀 closeout：widget 轻量续接

日期：2026-09-16。分支 `feat/widget-resume-95`（stacked 于 `feat/clip-frame-94c`）。

## 交付

1. **恢复端点** `GET /api/customer/sessions/current/messages`：Bearer（「current」=令牌所指）；active/ended/registered 照回 200（消息复用操作者详情端点 MessageOut 单一出处——形状含 citations/media_citations/tool）；401 与发问同口径；回执附 `rating`/`ticket` 两锚（评分回显与 handoff 表单回放的保真——评审认定合理非 creep）。
2. **前端存档恢复**（CustomerPage，widget 共用）：`ecustomer.session`（token+session_id+saved_at）localStorage；挂载试恢复——active 重放+续问、ended/registered 锁输入+横幅+评分仍可、401/403 清档走空态。
3. README widget 节「不在 v1」句随刀修订（goal 修订表既定项）。

## 证据

- **Owner 门禁亲验**：`--junitxml` **tests=1384, failures=0, errors=0, skipped=0**（+11）；ruff 过；前端 tsc/oxlint/build 过。
- **真栈三步（代理 Playwright 实录）**：①问句→reload 自动恢复（工具条/评分条重放）→续问同会话成功；②结束→reload：「会话已结束」横幅+历史+评分 5 星提交+再 reload 回显；③清 localStorage→空态新会话。
- **测试 11 例**：401 逐字同文案、三态 200、消息形状防漂移（对照操作者详情端点）。

## 评审实修（两轴子代理：无 P0/无 Standards 违规）

- **resume catch 全清档 → 401/403-only**：瞬时 500/网络错保留存档下次再试（评审 P2——一次抖动不再变永久弃续接）。
- 确证：消息形状单一出处有防漂移钉；80 刀口径不破（ended 发问 409/评分照旧）；重放与流式状态机无竞态。

## 边界声明（评审提示补记）

- 恢复端点与媒体 GET 同为无 IP 限流读端点（等值索引查）——演示栈可接受，公网部署时评估。
- localStorage 明文存令牌：访客形态本无更高保密面（XSS 无增量）；Safari 三方存储禁用已 try/catch 兜底。

## 后续

第 96 刀：使用剧本Ⅱ+演示三层（直播线端到端含洗帧环节 + 手册/demo_prepare/e2e 剧本测试）——第二梯队收官。
