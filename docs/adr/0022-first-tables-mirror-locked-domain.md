# 第一批表结构：领域 ADR 的直接映射

状态：随 `feat/governance-publish` 提出，PR review 把关。**本文只做已锁领域语义（`CONTEXT.md` + ADR 0002/0003/0005/0006/0009/0010/0013/0016/0019）到表的映射，不引入新数据语义。** 若 review 认定任何一条夹带新取舍，拦下改走 grill。

## 映射表

| 表 | 领域来源 | 关键列与约束 |
| --- | --- | --- |
| `operators` | 0016 一个商家能上线：真登录、一种操作者 | id、username 唯一、password_hash；无角色列（v1 无岗位 RBAC） |
| `products` | 0002 商品；0019 必填=文档×商品规格字段 | id、name、category、spec_schema JSONB（类目驱动：食品=净含量+保质期，器皿=净含量+材质）、spec_values JSONB（当前写回值 + 来源 `asset_id·version`） |
| `assets` | 0002 资产三态；0006 当前已发布版本指针 | id、kind（本刀仅 document）、status（ingested/pending_review/published，映射已接入/待人洗/已发布）、current_published_version_id 可空指针（0006：身份稳定指针可前移）；另含 `title`（展示名，登记时可选）与 `last_error`（机洗失败原因，0012 就地重试的运行状态）两列运行属性，不承载新领域语义 |
| `asset_versions` | 0006 资产版本=不可变快照；0003 对象键；0009 弽权 | id、asset_id、version_no、object_key（每版一把键不复用 0003）、extracted_fields JSONB（值或显式 `{abstained: true}`，禁止空字符串冒充 0009）、confirmed_fields JSONB（操作者确认/补填值）、published_at 可空；**行不可变**：进入 published 后禁止 UPDATE |
| `audit_log` | 0005/0016 审计=谁/何时/对哪条资产哪一版做了什么 | id、operator_id、asset_id、version_no、action（publish/confirm）、created_at；append-only，不进检索 |

## 语义对照要点（不许偏）

- 登记必须先有字节落对象存储才 INSERT 资产行（0013 没有 bytes 不能登记）。
- 发布为单事务：版本标 published → 资产指针前移 → 商品 spec_values 写回（0010）→ 审计落一行（0005）。
- 必填集合运行时由所挂商品的 spec_schema 派生，不是列（0019）。
- 修订（已发布资产开新待人洗版）与回滚**不在本刀**；表结构不得为它们预埋特殊列（防 speculative generality），下一刀按 0006 增列或新行再议。

## 工程选型（工程级，非领域）

SQLAlchemy 2.0 声明式 + Alembic 迁移；psycopg3；机洗同步执行（0012 任务不是中台对象，无任务表）。
