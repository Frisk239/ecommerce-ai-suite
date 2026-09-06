# Intake · 产品阶段原型（上一刀）

- 日期：2026-09-06
- 验收人：Slice Owner（会话）
- 对象：`docs/product-phase.md`（施工单）→ `prototype/`（交付物）
- 结论：**有条件通过**（条件与债务见下；不阻塞工程第 1 刀）

## Git 状态

- 仓库 `main` **零提交**，`origin/main` 已不存在（remote 为空仓）；`origin` = github.com/Frisk239/ecommerce-ai-suite。
- 全部内容未跟踪：`.gitignore`、`CONTEXT.md`、`docs/`、`prototype/`、`reference/`。
- 产品阶段无 `feat/*` 分支要合（按施工单约定，产品阶段只落工作区文件）。
- 处置：`feat/scaffold` 首笔提交携带产品阶段基线入库（见条件 2）。

## Evidence 抽查（2026-09-06 重跑）

| 项 | 结果 |
| --- | --- |
| `npm run build`（tsc -b + vite build） | ✅ 绿，4586 modules，0 错误 |
| `npm run lint`（oxlint） | ✅ 0 warnings / 0 errors |
| `prototype/verify/` 截图 | ✅ 34 张，含 v3-final-*（终审视觉）9 张、v4-*（流式输出）5 张 |
| README 验收记录 | ✅ 清单 A–G 全过 + 六轮修复记录 + 收口冻结口径（2026-09-05/06） |

## Spec vs claim 抽样

| 施工单承诺 | 核对 |
| --- | --- |
| 客服走真检索（`retrievePublished`，只命中已发布） | ✅ `prototype/src/store/selectors.ts` 存在，README 第五/六轮记录实测发布前后口径变化 |
| 发布闸门（必填空禁用 + action 层再校验） | ✅ `canPublish` 在 `selectors.ts`；第五轮记录 P0-2 修复 |
| 规格字段按商品类目 schema 驱动 | ✅ `specSchema` 贯穿 types/seed/store/selectors/Products |

## Safety

- 无密钥、无真实外部调用（纯前端 mock，符合施工单）。
- ❗卫生缺口：`.gitignore` 只忽略 `reference/*/`，**未忽略 `prototype/node_modules/` 与 `prototype/dist/`**。不修则在首次提交携带数百 MB 依赖与构建产物。

## 条件（进入脚手架首笔提交时执行，不需产品阶段返工）

1. `.gitignore` 补 `node_modules/`、`dist/`（覆盖 prototype 与未来 apps/web）等常规忽略。
2. `feat/scaffold` 首笔提交先落产品阶段基线：`CONTEXT.md`、`docs/`、`prototype/`（源码+README+UX-NOTES+verify 截图，不含 node_modules/dist）、`reference/README.md`、`.gitignore`；第二笔起为脚手架。

## 债务（记录，本刀不修；工程对照见 `prototype/UX-NOTES.md` 第四节冻结口径）

1. 全 mock：无登录、无真实模型调用、无 Postgres/对象存储。
2. 假对象键：`sourceContent` 字符串冒充字节，`oss://demo-bucket/...` 仅展示。
3. 同步任务：机洗/生成/训练点击即完成，无队列与失败重试。
4. 两套检索：客服（n-gram）与连接层（子串）各一份，工程须合成一套中台索引（ADR 0017）。
5. 登记是粘贴文本：工程要上传文件（ADR 0013 没有字节不能登记）。
6. 切片登记暗插素材任务：领域已改（ADR 0015 切片汇入是视图），原型未改。
7. `dispatch(PUBLISH)` 无操作者身份：工程要真登录（ADR 0016）。

## 交接给工程阶段的冻结口径

- **冻结**：交互与状态机（指导控制台开发）。
- **不冻结**：mock 大脑、假对象键、同步任务；`types.ts` 不是 DDL；mock 大脑不搬进 FastAPI。
- 视觉锚定：DSH/StaffDesk token（主按钮近黑 `#0F1115`、品牌蓝 `#4176E6` 仅语义、rgba 墨线四档分层）。
