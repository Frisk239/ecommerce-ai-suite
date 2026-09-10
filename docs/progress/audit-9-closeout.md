# 审计刀 9 closeout：第 46–48 刀三路并行只读审计（`feat/audit-9`）

日期：2026-09-11。范围：PR #72/#73/#74（第 46 刀切片真链路 + 第 47 刀可观测最小版 + 第 48 刀 CSAT），迁移 0023–0024；另核审计刀 8 结转的四条产品面缺口。方式：**三路独立只读子代理**（设计符合性 / 技术债与健壮性 / 功能缺口与产品面真实性），全部结论要求 file:line 或命令回显支撑；本人只做处置与实修。

## 三路结论

| 轴 | P0 | P1 | 说明 |
| --- | --- | --- | --- |
| A 设计符合性 | **无** | 1 | 14 条载荷性声称逐条对账，13 条属实；1 条（对象键扩展名恒一致）**有漏网端点** |
| B 技术债/健壮性 | **无** | 4 | 静默失败面逐项判过（多数「可接受」且有记录）；4 条真问题 |
| C 功能缺口/产品面 | **2** | 3 | 两条会让演示当场穿帮或让功能形同不存在 |

## 实修（本刀代码）

### P0

1. **CSAT 评分条对「只有拒答/转人工」的会话硬不可达**（C-P0-2）。闸门写成「有过 `kind='answer'` 的回答」，而只被拒答/转人工的会话恰恰是最想吐槽的那批人——**满意度只统计满意的人**（演示库 86 个会话里 31 个永远看不到评分条）。后端端点本来就只要求「会话 active」，是前端自己收窄的。修法：闸门改成「有过一次 AI 回答（任何 kind）」，空会话仍不打分。**复核**：`CustomerPage.tsx` 的 `hasReply` 判定 + 线上 bundle 实测（`vite` 服务的源码里 `hasReply = messages.some((m) => m.role === "agent" && !m.streaming)`）+ 真会话里评分条常驻可见。（诚实说明：演示库语料覆盖太广，**没能构造出真拒答来端到端点穿**——拒答路径靠代码判定与既有 19 条拒答会话的既有行为兜。）
2. **切片「上传即绑定」在演示库上已是空操作，且源录像绑上再无改绑入口**（C-P0-1）。演示库 27 条 pending 候选全部已绑（第 46 刀验收时绑过），再上传只回执「当前没有『尚无源录像』的待拣候选可绑定」——而 README 的 0:45 演示稿要求「先上传源录像再拣选」，台上照做即自相矛盾；且 `routes/clips.py` 只有三个端点，**绑错/换源录像无路可走**。**处置**：本刀只做「如实呈现 + 可复位」——把绑定现状写进 README 演示前检查（附解绑 SQL 复位命令），把「源录像重绑/替换」列为建议下一刀（改绑定模型属 intake 裁决 2 的产品决定，不擅自扩契约）。

### P1

3. **`open_revision` 不传 key_suffix：旧路径切片开修订会产出 `.mp4` 键装文本字节**（A-P1）。第 46 刀在 `PUT …/bytes` 上堵掉了「键说 mp4、字节是文本」，漏了姊妹端点 `POST /assets/{id}/revisions`（`make_object_key(asset.kind, content_bytes)` 不传 suffix，video 兜底 `.mp4`）。修法：新增 `_key_suffix(object_key)`，修订**沿用源版本的扩展名**。**钉测**：`test_revision_keeps_legacy_text_suffix`（旧路径切片 → 发布 → 开修订 → 新版本键仍是 `.txt`、正文端点仍返回时间码文本）。
4. **拣选无并发闸：同一候选可登记出两份资产**（B-P1-1）。两个并发 pick（双击/重试）都能过预检，各自 ffmpeg、各自登记——库里两份视频资产而候选只锚一份，另一份成孤儿证据；第 46 刀把窗口从毫秒级拉到 ffmpeg 的百秒级。修法：`_claim_candidate` 用条件 UPDATE（`WHERE status='pending'`）在 ffmpeg **之前** CAS 占位，影响行数 0 → 409；真切失败/源录像缺失时 `_release_claim` 放回 pending（否则「失败可重拣」是假的）。**钉测**：`test_claim_release_puts_candidate_back_to_pending` + 既有「切失败保持 pending」用例现在真的走过放回路径。
5. **上传源录像在事件循环里做 200MB 的 SHA256 与整块落盘**（B-P1-2）。`async def` 路由里同步做大文件 IO，会掐住同一循环上的顾客 SSE 流（而拣选路由本来就是同步 def）。修法：改同步 `def` + `file.file.read(MAX+1)`（与 `PUT …/bytes` 同形，FastAPI 丢线程池）。**钉测**：`test_upload_recording_is_sync_route`（路由不能是协程函数）。
6. **迁移 0023 未回填存量切片视频的 `transcript` 字段**（B-P1-4）。第 46 刀把 video 正文源从字节改成字段，但没回填 46 之前登记的切片资产——它们**一旦开修订重新发布**，正文块静默归零（发布成功、检索为空）。修法：新迁移 **0025** 回填（只动「kind=video 且键 `.txt` 且无 transcript」的行，值从候选表 `transcript` 反查；取不到就跳过，不编造）。**复核**：演示库迁移后资产 11/12/16 三条旧切片都拿到了 `transcript` 字段；迁移在临时库上 `upgrade head → downgrade 0024 → upgrade head` 往返通过。
7. **46/48 两刀的产品动作在 47 刀的观测面上完全不可见**（C-P1-1）：切段失败率、评分分布都只能翻库。修法：新增两个有界标签指标 `clip_cuts_total{result}`（ok/failed/legacy）与 `csat_ratings_total{score}`，并在跑通的三处补结构化日志（源录像登记、切片失败带 candidate 与原因、会话评分只记 session 与分数、**不含留言内容**）。**钉测**：`test_clip_cut_and_csat_metrics_registered` / `..._counters_increment` / `test_rating_records_csat_metric`。

### P1（文档/CI，已修）

8. **CI 未声明 ffmpeg**（B-P1-3）：真切片契约（真切 mp4 / 失败保持 pending / 发布切块来自字段）在无 ffmpeg 环境会整例 skip——runner 一变就静默变绿。修法：CI test job 显式 `apt-get install ffmpeg` + `refuse skip-green` 段加 `command -v ffmpeg` 守卫。
9. **README 完全没提第 48 刀**（C-P1-3）+ **「凡亮『已转人工』徽章的地方背后都有工单」是绝对声明而演示库有三条 42 刀前存量会话无工单**（C-P1-2）。修法：0:00 段补 CSAT 闭环（评分条/总览满意度/两向 thumbs）、绝对声明改为「新产生的会话凡亮徽章都有工单」并注明历史存量；「观测」节补两个新指标。

## 记债（未修，逐条给出理由）

**产品面（需 Owner 裁决）**

- **源录像不可重绑/替换**（C-P0-1 的功能部分）：绑定模型是第 46 刀 intake 裁决 2 定的「上传即绑、已绑不改」，改它要动契约（新增 rebind/replace 端点或上传参数）。**建议下一刀优先做**——它同时解掉「绑错即永久锁死」与演示穿帮。
- 商品价只覆盖 **3/115**、多来源数据（Wikidata 91 / OFF 20 / reviews 200 / WANDS 30）在产品面不可见、工作队列首屏仍 **193 行（183 条 upload）** 原始灌入、widget 只落 visitor_id 不落宿主 origin——审计刀 8 结转的四条，本次只读复核**全部仍然成立**（数字已更新进本节）。演示库里 11/12/16 三条已发布视频的字节仍是旧的 `.txt` 时间码文本（故事口径已改「真 mp4」，历史资产不追改——已在 closeout 记过）。
- CSAT 面板无「去会话」深链（低分留言跟不回上下文）；评分不可改不可删、留言无全量页（第 48 刀已记）。

**工程面（本刀新记）**

- 切片拣选整份源录像进出内存（≈2× 录像大小峰值）、`ClipPickIn.ids` 无长度上限（单请求可触发 N 次 ffmpeg）、上传只校验扩展名不校验魔数。
- `register_recording` 先写对象后写库（DB 失败留 200MB 级孤儿）；ffmpeg 临时目录在系统 temp（不随 `STORAGE_ROOT`）。
- 迁移索引：`clip_candidates.recording_id` 与 `session_ratings.created_at` 无索引（当前行少，量增后扫表）；`session_ratings.score` 无 CHECK（合法值集在服务层）。
- 口径重复：评分档位/留言上限/键后缀/指标标签集合在多处字面量（后端 2 处、前端 3 处），改一处要动多处；video 正文取值有 `resolve_field_value`（认 abstained）与 `retrieval._field_text`（不认）两份实现（当前 transcript 无 abstained 故等价）。
- 观测面细账：`record_chat_request` 只在 `run_ask` 成功后记（500 的失败发问只进 HTTP RED）；`kind` 标签无白名单（新 kind 会静默新增标签值）；`ttft/token` 的 `model` 标签取进程级 `get_settings()`（自定义 Settings 建 app 时可能与实际调用不一致）；`structlog.contextvars.clear_contextvars()` 清的是全部绑定（当前无其他绑定者）。
- 测试面：评分并发双提交（IntegrityError→409）无直测且判定靠约束名字符串匹配；CSAT 集成用例模块内共享库、个别断言依赖前序用例是否已评分；`test_csat.py` 有一条「越界分不进分布」的口径曾与实现不一致（已在本刀统一为「三处一致忽略」）。

## 门禁

集成 **886 → 892 passed / 0 failed / 0 skipped**（新增 6 例：修订键后缀 / CAS 放回 / 上传同步路由 / 两个指标存在性 / 评分指标增量；另有既有用例在新 CAS 下继续通过）；ruff 全过（apps + packages + scripts）；前端 build 绿、lint 7/0；迁移 0025 在演示库实测回填 3 行、在临时库往返可逆。真容器复核：`/metrics` 含 `clip_cuts_total`、`csat_ratings_total` 的 TYPE 行；顾客页评分条常驻。

## 下一刀建议（三选一，按痛感排序）

1. **源录像可重绑/替换**（小-中）：解 C-P0-1 的「绑错锁死」与演示自相矛盾；顺带把上传回执改成「本次绑定 N 条、切出片段来自《label》」。
2. **商品价 + 来源可见**（小-中）：解审计刀 8 结转最久的「115 件只有 3 件有价」与「四个真实数据源在 UI 里全叫 upload」——连续四份 closeout 点名。
3. **CSAT 闭环补全 + 低分深链**（小）：低分留言可点回会话、评分可改、留言列表页，以及把「拒答会话打不了分」的真实拒答端到端钉一次。
