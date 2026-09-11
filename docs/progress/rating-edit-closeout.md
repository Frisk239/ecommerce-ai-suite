# 第 71 刀 closeout：评分可改（覆盖式留最新）

日期：2026-09-11。依据：Owner 裁决（完善路线批）。**迁移 0029**（session_ratings.updated_at）。

## 交付

| 件 | 交付 |
| --- | --- |
| 迁移 0029 | `session_ratings.updated_at`（nullable timestamptz；NULL=首评未改） |
| 后端 | 已评 → UPDATE 覆盖（score/comment 整体覆盖 + updated_at），不再 409；RatingOut 带 updated_at；唯一约束不动（并发插入竞态兜底不变） |
| 前端 | 评过不再折叠：星星回显当前分（hover 预览）、留言框回填、点星即再提交；状态行「已提交 N 星（，已更新），可随时修改」；新会话归位含新 state |
| 指标口径 | `csat_ratings_total` 计每次提交（含改评）——事件数口径；CSAT 均分/分布读行数据（恒最新） |

## 验收（浏览器实测，playwright-cli 真栈顾客页）

| 步骤 | 结果 |
| --- | --- |
| 问一句（净含量）→ 评分条出现 | ✓「这次服务怎么样？点星星即提交」 |
| 点 4 星 | 「已提交 4 星，可随时修改」；DB score=4 |
| 填留言「改分前先留言」→ 点 5 星 | 「已提交 5 星，已更新，可随时修改」；DB score=5、comment 保留、updated_at 非空（对照旧行 98：updated_at NULL） |

**门禁**：集成 **1157 → 1158 passed / 0 failed / 0 skipped**（改判 1 例 + 评审补面 1 例：
`test_rating_is_editable_latest_wins`（首评 updated_at=None/改评覆盖单行/comment 不带即清空/
created_at 不动）与 `test_rating_edit_validations_and_metric`（改评同套 422 校验、指标计每次
提交、操作者详情读最新））；前端 lint 7/0、build 绿；检索金标逐位相同（70.0/65.0）。

## 评审处置（两轴评审：P1×1、P2×5、P3 若干，全数实修/订正）

- **P1 文档漂移（实修）**：README「评过即收」、customer.py 模块 docstring「一会话一评（已评
  409）」、endpoints.ts/types.ts/service.py 的旧措辞全部改口径；types.ts `SessionRating` 补
  `updated_at` 字段。
- **P2 时钟源混用（实修）**：`updated_at` 改 `func.now()` DB 钟（SQL 表达式 flush 时求值）——
  与 created_at 的 server_default 同源；Python/DB 双钟在分机部署会假翻转。
- **P2 指标口径措辞（订正）**：仪表 CSAT 窗口/排序钉在 `created_at`——改评对**窗口外旧行**
  的均分不可见（窗口口径，非「恒最新」）；closeout 表述已限定。
- **P2 测试补面（实修）**：见门禁行。**测试基建顺带修**：conftest 按 login 闸先例放宽顾客
  闸（csat 模块 20+ 发 /rating+/messages 撞 30/60s IP 闸的边缘——评审探针 +2 发即穿）；
  限流语义由 `test_rate_limit` 自建闸钉，不受影响。
- **P3**：「可随时修改」在非 active 会话会 409（与发问同闸）——前端顾客页只在 active 可用，
  不另改文案；migration header 类型标注对齐同侪；csat-closeout 的「改评待裁决」记债由本刀
  取代（历史文档不改，此处记一笔）。

## 诚实披露

- **没有评分历史**：覆盖式留最新是裁决语义；改评前的分数/留言不留存（要看趋势得靠
  `csat_ratings_total` 事件计数与日志行「会话改评: session=X score=Y」）。
- comment 整体覆盖（不带即清空）依赖前端回填——第三方直接调 API 不带 comment 会清留言，
  这是接口语义（覆盖式），不是 bug；已在端点 docstring 写明。
- 评分条从「评过即收」变「常驻可改」：页面底部多占一行——取舍已由 Owner 裁决背书。

## 后续

- 第 72 刀：演示素材修复（G4/G5「你们几点上班」——补口径资产正文真正覆盖缺口问句）。
- 完善路线余项：73 多轮工具追问 → 74 空 schema 治理 → 75 来源权重；审计刀 15 于 75 后。
