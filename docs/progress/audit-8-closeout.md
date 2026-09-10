# 审计刀 8 closeout：三路并行审计（`feat/audit-8`）

日期：2026-09-10。范围：审计刀 7（PR #50，第 40 刀后）以来的**全部合并**——PR #55–#70，即**第 41–45 刀**（含 UI/UX 平行工作流 6 包、走查修复、缺口来源会话），**迁移 0018–0022**。三路独立只读子代理：**设计符合性 / 技术债 / 功能缺口**。

## 总判定

**三路 P0 全零。** 主干契约（价格字段与目录回落、退货真迁移、令牌 TTL、转人工工单、仪表聚合、可嵌入小组件）都是真实现且有钉测；两份本阶段 ADR（0045/0046）的每条硬决定都能在代码里指到落点；没有把没做的写成做了的**结构性**虚报（仅一处措辞超额，已订正）；安全面（PII 出口掩、令牌过期、嵌入来源闸）无虚假声明。

## P1 实修（5 条）

| # | 来源 | 问题 | 修法 |
| --- | --- | --- | --- |
| A | 技术债 | **顾客面泄露两阶段写的写动作凭证**：`check_return_eligibility` 的 `confirmation_token` 写进工具条 result，SSE 两通道同形状下发 → 顾客浏览器里能看到这份 create_return 的唯一授权凭证（ADR 0044 的边界是「顾客与模型都不掌握创建权」；审计刀 7 已记，一直未修） | `chat_engine.customer_safe_tool`：顾客通道（`expose_gap_id=False`）下剥掉 `待确认 token=<hex>`，其余形状不变；**库内持久化的 tool 列不动**——操作者重开会话仍能拿到令牌去确认。两侧都有钉测（操作者必须有 / 顾客必须无） |
| B | 技术债 | **首问标题弱掩码**：`routes/service._first_question` 用 `redact`，漏 `138-0013-8000`（带分隔）与 `ab@x.co`（短域）——而它的产物是**对话资产 title**，随 MCP get_asset/export/search 与详情页外流（违反 0038「出口必掩」） | 改用第 42 刀新增的 `redact_contact`（多掩可接受，正是 0046 判例）；补纯函数钉测 |
| C | 功能缺口 | **令牌过期是死路**：24h 后顾客只看到通用错误「会话不存在或令牌无效」，输入框还开着、每次发问都 401 —— 45a 把它推给 45b、45b 没接（交接断档） | 顾客页识别 401 → 明确横幅「访问令牌已过期（有效期 24 小时）」+「重新开始」按钮（重新签发），并锁住 composer；`canAsk` 加 `!expired` |
| D | 功能缺口 | **缺口「去补文档」静默开修订**（`ui-ux-plan` §四 UX-C.3 明文禁止，双击还会 409） | 该商品已有已发布规格文档时先弹选择：**在既有文档上开修订** / **另起一份新文档**（后者走登记抽屉），不再直接 `openRevision` |
| E | 设计符合性 | **声明超额**：`gap-source-session-closeout` 称「A5b 至此全部落地」、CONTEXT 称「走查缺陷全修」——而 A5b 只做了一半（来源会话链接做了，**商品猜测没做**，拒答路径有意不猜商品） | 两处订正为「A5b 只做了一半 + 商品猜测未做」 |

## P2 实修（顺手清）

- **`.env.example` 与 README 环境变量清单**补 `CUSTOMER_TOKEN_TTL_SECONDS` / `WIDGET_ALLOWED_ORIGINS`（45a/45b 新增但没同步；README 还写着「见 .env.example」）。
- **`triage_asset_ids` 下沉服务层**（新 `services/triage.py`）：它被 `routes/stats.py` 从 `routes/customer.py` 导入，形成全仓唯一的 route→route 导入——第 42 刀刚把同类坏味道消掉，这次是回潮，按同一纪律收口。
- **`_widget_gate` 来源比较加小写归一**：浏览器报的 origin 主机名恒小写，白名单手写成大写不该误拒。
- **`confirm_return` 补行锁 `with_for_update()`**（只加在确认这处；只读的资格查询不加锁）。审计指出 cleanout 里「并发会重复事件」的描述**不准确**——真实风险是「后写覆盖先写」的 lost update（当前两写相同故无害）。
- **两页 Esc 停止监听收敛到 `useEscapeClose`**（此前 `CustomerPage`/`ServicePage` 各抄一份同样的 window 监听）。
- **文档漂移 8 处**：`slices.md` 补删一条被截断的重复「审计刀 7」标题、上一刀指针（44→45）、41 刀口径（前 20 件→已定价前 8）、42 刀计数（764→780）；`CONTEXT.md` 的 golden 计数（13→16）；`ui-ux-plan.md` 过期指针（「下一刀=第 42 刀」→ 41–45 已交付）；roadmap 的 embed.js 体积（「~2KB」→ 实测约 5.7KB）；ADR 0045 补注第 4 道政策词闸与「全店未定价时列举仍返回答案」；ADR 0046 补注 `redact_contact` 与 `note` 也掩。

## 记债（未修，均 P2/P3，如实列出）

**产品面真缺口**：
1. **115 件商品只有 3 件有价**（`SELECT count(*), count(price_cents) FROM products` → 115/3）：roadmap 第 41 刀承诺的「演示价=类目基准价脚本生成」没落在 Wikidata/OFF 导入商品上，所以「多少钱」目前只对 3 件成立。**这是最容易被一句话 grill 穿的点**。
2. **多来源数据在产品面不可见**：Wikidata 91 件（全 NULL 价）、OFF 20 件、评论 200 条、WANDS 切片 30 条都在库里，但 UI 把它们统一压成「上传」来源，根 README 零提及四个数据源——「多来源」这个设计点只活在 `docs/research/` 与 `scripts/realdata/`。
3. **工作队列首屏仍是 183 条原始灌入货**（待人洗 190 行里 183 是 upload）——Owner 一直未裁决；UX-E closeout 已自认「数据倾倒观感未根治」。
4. **widget 只落访客 id、不落宿主 origin**：白名单可配多站点，但操作者无法分辨这条会话来自哪个嵌入站（`models.py` 明写「不加 origin 列」）。
5. **UX-C/F/G 仍有未做项**：C3 已修，但 C2b（商品猜测/「这条缺口没有商品」说明）、C5（已解决同问回链）未做；F1（切片灰底假封面）、F3（连接器两卡标「请用 MCP 客户端」）、F4（顾客空态「一次性会话身份」措辞）未做；G1 的侧栏「总览」项与可点面包屑、G3 的切片拣选/素材抽检确认、G5 的 390 卡片化未做。
6. **令牌已过期在顾客面没有「会话已过期」的专属文案**（本刀修了死路：给了横幅+重新开始；但服务端仍与「令牌无效」同 401 同文案——那是有意的防探测口径）。

**技术债**：
7. `service_sessions.customer_token` 与 `expires_at` 的配对不变式**只靠应用层单点写入维持**（无 DB CHECK）；将来的直插路径会造出「有令牌无过期」的永久 401 行。
8. 迁移 0018 的 downgrade 在用过改价功能后**必然失败**（docstring 已声明「属预期」，但等于不可逆）；0019/0020/0021/0022 无回填/降级测试（0021 是安全相关回填，CI 空库命中 0 行测不到——已在 45a closeout 如实记录）。
9. `feedback` 的 `@> '{"helpful": false}'` containment 无 GIN 索引，且 stats 每次导航扫两遍（195 行无感，属「下一迁移顺手补」）；`service_messages.created_at`/`service_sessions.created_at` 无索引（43 刀已记债）。
10. 会话列表为取每会话首问把全部 customer 消息正文拉进内存（无 LIMIT）；`stock_tools`/`knowledge_gaps` 的失败日志打原始问句（可能含 PII，非响应出口）。
11. `useEscapeClose` 的 `onClose` 每渲染新建致 effect 重挂（已记两次，未修）；390 卡片化（G5）与 `aria-controls/tabpanel` 仍缺。

## 门禁

- 集成 **802 → 804 passed / 0 failed / 0 skipped**（新增两条 P1 钉测）；`ruff check apps/api packages` 全过。
- 前端 `npm run build` 绿、`npm run lint` **7 warnings / 0 errors** 与 main 基线逐数一致。
- 无新依赖、无新迁移（审计刀只修不扩）。

## 诚实披露

- 三路审计的「P0 零」是**只读审查**结论，不等于「没有问题」——上列记债里有 4 条是我认为会被人当场 grill 的产品面缺口（尤其**商品价只覆盖 3/115** 与**多来源在产品面不可见**），它们不是本刀能顺手修完的，需 Owner 排刀。
- 本刀**未**改任何评测路径，`docs/research/rag-eval-report.md` 基线与 45 刀后一致（65.0/75.0）。
- 审计的 P2 里有 3 条我**有意不修**：compose 的 `WIDGET_ALLOWED_ORIGINS` 默认值仍放行 `http://localhost:5173`（本地演示宿主页要能开箱即用，已加注释说明「生产改成商家 origin」）；`0018` 的 downgrade 语义（改价留痕是 append-only，写死为可逆反而危险）；widget 白名单的「伪造头」残留面（要边缘层才能堵，README 已披露）。

## 后续

- **下一刀（roadmap 第 46 刀）**：直播切片真链路——上传真 mp4 作源录像、ffmpeg 按时间码切出**真实片段字节**（现在是「时间码文本」冒充）；走 slice owner 全流程。
- 建议在 46–48 之间（或紧随审计）排一刀处理上面记债第 1/2 条（商品定价补全 + 多来源可视），它们直接决定产品第一眼的说服力。
