# Closeout · 工程第 2 刀：治理发布写回（feat/governance-publish）

- 日期：2026-09-06
- 分支：`feat/governance-publish`（三笔 `671b381` ADR 0022 → `4edddf0` 实现 → `b55a6b9` 评审修复）
- 合并：**已进 main**（人类经 PR #2 手动合并，merge commit `77f732c`，2026-09-06T08:05Z；PR #1 脚手架同日先合并）
- 短对齐：`.scratch/governance-publish/spec.md`（本地）

## 交付（用户路径全程）

操作者登录 → 上传规格文档挂商品（字节进 `LocalDirectoryStorage`，登记=已接入，记录只有对象键）→ 机洗按商品类目 specSchema 抽字段（弃权显式）→ 待人洗确认/补填 → 发布（单事务：指针前移+写回商品+审计）→ 商品页规格更新带 `A-{id} · v{N}` 来源芯片。

- **登录**（ADR 0016 最小）：bcrypt + httpOnly 签名会话；除 `/health`、`/api/auth/*` 全鉴权（含读接口，裁决依据=词表「控制台=登录后打开」）
- **第一批表**（ADR 0022 映射）：operators/products(specSchema)/assets(三态+指针)/asset_versions(不可变+对象键)/audit_log(append-only)；Alembic 可逆
- **登记**（0013）：字节先落存储才 INSERT；text/md ≤2MB
- **机洗**：净含量/保质期/材质；弃权 `{abstained:true}`；否定词黑名单（分隔式+后缀式）+ 日期陷阱剔除；失败停已接入就地重试（0012）
- **发布闸门**：必填=文档×商品规格字段（0019）；missing/unconfirmed 两类 422；**写回只取确认过的字段**（0010，评审修复）；四步单事务（0005/0006）
- **控制台**：登录/三态列表/详情（弃权显性、一键确认、补填、写回预告、已发布只读）/商品页/404；视觉锚 prototype v3；修 vite proxy 前缀缺陷

## 证据（Owner 亲跑，输出原文）

| 项 | 输出 |
| --- | --- |
| `uv run pytest`（根目录） | `64 passed, 14 skipped, 2 warnings in 6.19s`（skip=无测试库时集成组） |
| `SUITE_TEST_DATABASE_URL=… uv run pytest` | `78 passed, 9 warnings in 9.35s` |
| `uv run ruff check .` | `All checks passed!` |
| web build / oxlint | `✓ built in 299ms` / `2 warnings, 0 errors` |
| 浏览器点穿（IAB 1280×720） | 登录守卫→登记（「未标注材质牌号」未误抽）→材质弃权补填 316不锈钢→确认净含量→发布 v1（写回预告确认框）→商品页 `316不锈钢/500ml` + `A-1 · v1` 芯片可点；瓶装水未写回显示 —；留痕 3 条（发布+确认×2） |
| 匿名读 | `GET /api/assets` → 401 |
| 字节落盘 | `data/objects/documents/{uuid32}/{sha16}.txt` 内容完整 |

## 评审（/code-review 两轴）与修复

硬违规 0。修复 4 项（`b55a6b9`）：写回只取确认过的字段（含测试改写）；机洗否定词黑名单补分隔式；保质期日期陷阱剔除（「生产日期：2026年8月1日，保质期：12个月」→12个月）；UI 去掉越刀的「可被检索」承诺文案（检索属第 3 刀）。ADR 0022 补记 `title`/`last_error` 运行列。

## Deviations（如实）

1. gh CLI API 被网络层劫持（api.github.com 直连 301 到 github.com → 406/烂响应）；设 `HTTPS_PROXY=7890` 后恢复。PR #3 因此误开，已关闭并留言（代码实际经 PR #2 进 main）。
2. 人类在会话中段用 GitHub compare 按钮开 PR #2（自动标题）并合并，合并范围含全部三笔——含评审修复，无需补合。
3. 测试计数口径：`apps/api` 目录跑=54（仅 api），仓库根=64（含 platform 10 个）——以根目录为准，已写进本表。
4. IAB 内置浏览器不支持文件选择器：上传经 API 完成（multipart Python 脚本），UI 文件控件/校验经 DOM 验收；curl 在 Git Bash 对 multipart 表现异常，已换 urllib。

## 债务（进下刀施工单参考）

1. 修订流与回滚（ADR 0006/0016 语义已锁，UI 已留「修订流在后续刀交付」文案）。
2. 机会洗扩展：仅 text/md；图片/视频/对话/素材种类未开。
3. `assets.py` 的 spec_schema 拷贝模式重复四处；前后端「取值规则」双实现缺一致性测试钉住。
4. `test_sessions.py` 假断言（`assert time.time()>0`）；集成测试顺序耦合（module 级共享登录态）。
5. 会话 cookie 未设 `Secure`（生产 HTTPS 后开启）；`OPERATOR_PASSWORD` 默认值仅限开发。
6. 继承：compose web 为 dev server 形态；生产构建 `/api` 无反代。

## 下一 Owner 注意

- main 已含两刀（脚手架+治理发布写回）。第 3 刀**待短对齐**，候选：**B 客服引用**（首选——检索对象已存在，ADR 0004/0017/0018/0021）、修订流+回滚、MCP 只读已发布（0001/0020）。
- 本机 gh 需 `HTTPS_PROXY=http://127.0.0.1:7890`（api.github.com 直连被劫持）。
- 浏览器验收上传类交互时 IAB 无文件选择器，用 API 补链路。
