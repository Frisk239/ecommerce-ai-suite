# scripts/realdata —— 多来源真实数据灌入（数据刀 I–III）

只经既有通道灌公开数据。仓库入脚本与小 fixture；dump/下载物落 `out/`（gitignore）。

**第 33 刀目录主数据 = Open Food Facts 公开 dump，不是手写种子。** 流式读 TSV.gz，清洗（条码+品名+可解析净含量），规格正文只用源字段，走登记→机洗→确认→发布。不编造保质期。

## 数据源与许可

| 来源 | 内容 | 许可 | 出处 |
|---|---|---|---|
| **Open Food Facts dump** | 夜更 TSV.gz（code/product_name/brands/quantity/ingredients…） | **ODbL** | <https://world.openfoodfacts.org/data> · [CSV.gz](https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz) · [字段表](https://world.openfoodfacts.org/data/data-fields.txt) |
| Wikidata SPARQL | 商品类条目（类目+名称标签） | **CC0** | <https://www.wikidata.org> |
| online_shopping_10_cats | 6.2 万条中文电商评论 | 研究用途 | [ChineseNlpCorpus](https://github.com/SophonPlus/ChineseNlpCorpus) |
| ABCD | 英文客服对话 | **MIT** | [asappresearch/abcd](https://github.com/asappresearch/abcd) |
| WANDS | 家具检索标注 → 切片候选 | **MIT** | [wayfair/WANDS](https://github.com/wayfair/WANDS) |

网络：标准库 urllib 自动 respect `HTTPS_PROXY` / `HTTP_PROXY` 环境变量（宿主代理
`http://127.0.0.1:7890` 时直接可用）。Wikidata 查询服务当前激进限速约 1 请求/分钟：
脚本带 User-Agent 礼貌头，429 按 `Retry-After`（缺省 65s）退避重试，实跑一次
全链拉取约 1-2 分钟属正常。

## 通道映射（与六来源通道的关系）

- **OFF dump → 食品目录（第 33 刀）**：`load_openfoodfacts.py` 流式清洗 → `--load` 灌 products（schema 仅净含量）→ `--register --publish` 把 dump 原文规格走既有 register/确认/发布，写回 spec_values。
- **商品 → 种子通道**：products 无写端点，`fetch_wikidata_products.py --load` 用同仓
  uv 环境 `from suite_api.models import Product` + SQLAlchemy 直连 `DATABASE_URL`
  幂等灌入（name+category 已存在跳过）。不动 conftest 测试种子（spec 裁决）。
- **评论 → 上传通道批量形态**：`load_reviews.py --import` 登录 operator 后调既有
  `POST /api/assets/import-csv`（200 行/批，title/content 表头）——证明批量通道
  吃得下真实第三方数据；`--publish N` 走既有 `POST /api/assets/{id}/publish`
  （评论资产不挂商品，无必填字段闸门）。
- **ABCD 对话 → 会话回流通道**：`load_abcd_dialogues.py --register` 直调既有
  `services/registration.register_asset(kind=dialogue, source_kind=session_backflow)`
  （与 `POST /api/sessions/{id}/register` 同一函数）：字节落共享 storage root，
  机洗=真 LLM 抽 QA 草稿推进待人洗；`--publish K` 经 API 走既有
  `PATCH /api/assets/{id}/versions/1/fields`（qa_pairs 确认）+ publish。脚本自动
  读仓库根 `.env` 拿 LLM 凭证（无 key → QA 弃权降级，资产照常待人洗）。
- **WANDS 对 → 切片拣选通道**：clip_candidates 是切片模块自有种子（无写端点），
  `load_wands_clips.py --load` 直连 DB 幂等灌入（timecode+transcript 已存在跳过）。
  `product_id` 不可空（模型实测），幂等造专属商品「WANDS 家具（演示）」承载全部
  候选；相关性档位拼进 transcript 尾注 `（标注：Exact）`（模型无独立列）。

## 全链命令（仓库根目录）

```bash
# 0. 起栈（本机 5432 被占时 .env 设 PG_PORT=5433，宿主端口即 5433）
docker compose up -d

# 1. Open Food Facts dump：流式清洗 N 条 → 灌库 + 登记发布（ODbL）
uv run python scripts/realdata/load_openfoodfacts.py --n 80 --load --register --publish \
    --db postgresql://suite:suite@localhost:5433/suite --api http://localhost:8000

# 1b. Wikidata：SPARQL 拉取 → out/products.csv（--load 同时直连灌库，幂等可重跑）
uv run python scripts/realdata/fetch_wikidata_products.py
uv run python scripts/realdata/fetch_wikidata_products.py --load \
    --db postgresql://suite:suite@localhost:5433/suite
# 1c. 数码外设四类（第 90 刀，QID 2026-09-16 实测修正）：--digital-only 只拉
#     键盘/鼠标/显示器/耳机（attrs 含 P176 品牌/P571 年份/P2048-P2049 高宽）；
#     WDQS outage 时 429 按 Retry-After 退避（可达 1000s/查询），逐查询缓存续跑
uv run python scripts/realdata/fetch_wikidata_products.py --digital-only --load \
    --db postgresql://suite:suite@localhost:5433/suite
# 1d. 数码规格文档（第 90 刀）：有品牌的耳机/显示器 → 治理通道登记→人洗确认→
#     发布写回（顾客问「X 什么品牌」可命中）；幂等锚 (title, product_id)
uv run python scripts/realdata/publish_digital_specs.py \
    --db postgresql://suite:suite@localhost:5433/suite   # 宿主连 compose db
#   compose 容器内网络则用 --db postgresql://suite:suite@db:5432/suite
#   也可不传 --db，直接 export DATABASE_URL=... 后 --load

# 2. 评论：下载 zip（缓存 out/）→ 固定种子抽样 → out/reviews.csv
uv run python scripts/realdata/load_reviews.py --n 2000

# 3. 导入+发布样例：登录 operator → 200 行/批打 import-csv → 抽 20 条发布
uv run python scripts/realdata/load_reviews.py --n 200 --import --publish 20 \
    --api http://localhost:8000

# 4. ABCD 对话：下载 gzip json（缓存 out/）→ 固定种子抽会话 → 转写
#    → out/abcd_transcripts.txt；--register 直调 register_asset（回流语义，
#    真机洗 QA 草稿；storage root 默认 ./data/objects 与 API 容器共享）
uv run python scripts/realdata/load_abcd_dialogues.py --n 60
uv run python scripts/realdata/load_abcd_dialogues.py --n 60 --register \
    --db postgresql://suite:suite@localhost:5433/suite
#    登记 + 抽 8 个经 API 确认 QA（PATCH qa_pairs）并发布
uv run python scripts/realdata/load_abcd_dialogues.py --n 60 --register --publish 8 \
    --api http://localhost:8000

# 5. WANDS 切片候选：三件 csv 下载缓存 → Exact join → 抽样 → out/wands_clip_candidates.csv
#    → --load 直连幂等灌 clip_candidates（附承载商品行「WANDS 家具（演示）」）
uv run python scripts/realdata/load_wands_clips.py --n 30
uv run python scripts/realdata/load_wands_clips.py --n 30 --load \
    --db postgresql://suite:suite@localhost:5433/suite
```

| 脚本参数 | 说明 |
|---|---|
| `fetch_wikidata_products.py --limit/--per-category/--seed/--out` | 总行数上限（默认 200）/每类上限（40）/stock 种子（42）/CSV 路径 |
| `fetch_wikidata_products.py --digital-only/--digital-limit/--timeout` | 只拉数码四类（第 90 刀 QID 常量）/ 数码类独立行数上限（默认 200，与 legacy 六类的 `--limit` 分离——审计 18 P2#3）/ 单查询超时秒 |
| `publish_digital_specs.py --csv/--api/--user/--pass/--db` | 规格文档治理发布（第 90 刀）：fetch 输出的 products.csv / API 基址 / 操作者凭证 / 演示库 URL（默认 .env/DATABASE_URL） |
| `load_reviews.py --src/--zip-file/--cache` | zip 下载地址覆盖 / 本地 zip 直读 / 缓存路径 |
| `load_reviews.py --n/--seed/--import/--publish/--api/--user/--pass/--db/--cats` | 抽样条数（默认 2000）/种子 / 登录批量导入 / 发布 N 条（须与 --import 同跑）/ API 基址（默认 `http://localhost:8000`）/ 操作者凭证（默认 operator/operator123，开发种子）/ 目标库 URL（默认 .env/DATABASE_URL）/ 类目白名单逗号分隔（第 90 刀，空=不过滤） |
| `load_abcd_dialogues.py --n/--seed/--cache/--out` | 抽样会话数（默认 60）/种子（42）/gzip 缓存 / 转写输出路径 |
| `load_abcd_dialogues.py --register/--db/--storage-root/--env-file` | 直调 register_asset 登记对话资产 / 目标库 URL（默认 .env/DATABASE_URL）/ 对象存储根（默认 .env/STORAGE_ROOT 或 ./data/objects，须与 API 容器一致）/ 启动前载入的 .env（默认仓库根，LLM 机洗凭证来源） |
| `load_abcd_dialogues.py --publish/--api/--user/--pass` | 对最新 K 个登记资产经 API 确认 QA+发布（须与 --register 同跑）/ API 基址 / 操作者凭证 |
| `load_wands_clips.py --n/--seed/--cache-dir/--out` | 抽样相关对数（默认 30）/种子（42）/三件 csv 缓存目录 / 候选预览 CSV |
| `load_wands_clips.py --load/--db/--env-file` | 直连灌 clip_candidates（幂等）/ 目标库 URL（默认 .env/DATABASE_URL）/ 启动前载入的 .env（默认仓库根） |

转换落点：商品 → products 行（spec_schema/spec_values 空 JSONB、stock 0-99 固定种子）；
评论 → `(title, content)`，title = `{类目}评论 · {前 18 字}`，content = 评论全文；
ABCD → kind=dialogue 资产（source_kind=session_backflow），转写=`顾客：…/客服：…`
按行（与回流端点同构），title = `{flow}/{subflow} · {顾客首问截 30 字}`；
WANDS → clip_candidates 行（status=pending，timecode 自序号合成 40s/段自增，
transcript = `顾客问 {query} —— {product_name}（标注：Exact）`）。

## 幂等性（重跑风险）

| 写路径 | 重跑行为 |
|---|---|
| Wikidata `--load` | 幂等，可重跑（name+category 已存在跳过） |
| `publish_digital_specs.py` | 幂等，可重跑（(title, product_id) 锚：已发布跳过、未发布续走确认+发布；单行 HTTP 失败不拖垮整跑，重跑自愈） |
| OFF `--load` | 幂等，可重跑（name+category 已存在跳过） |
| WANDS `--load` | 幂等，可重跑（timecode+transcript 幂等键，全跳过） |
| **reviews `--import`** | **重跑重复——import-csv 不做内容去重，同一 csv 重跑再建一批新资产** |
| **OFF `--register`** | **重跑重复登记——同一行重跑再登记一条新资产** |
| **ABCD `--register`** | **重跑重复登记——register_asset 直调无内容去重** |

## 测试

`apps/api/tests/test_realdata_scripts.py`（38 例，离线）：转换纯函数 + `samples/`
fixture 断言，含交叉验证——脚本产的批字节能被既有 `parse_import_csv` 直接受理；
ABCD 转写与回流端点同构、WANDS 候选形状与模型列宽对齐；第 90 刀起含数码
QID 映射、米→厘米换算边界、--cats 类目筛选、规格文档候选/正文/标题形状。网络/DB
路径不在单测范围（实跑即验）。

## 边界（后续刀再动）

JDDC（注册门槛）、评论类目→商品挂接、WANDS 视频本体与真时间码、Open Food
Facts 连接层登记、直播数据集（观察项）、定期同步。

> 商品规格文档接线：**已接线（第 90 刀）**——P176 品牌等属性经 `publish_digital_
> specs.py` 走治理通道登记发布（2026-09-09 交接时删除的死路径不复用；P2067 质量
> 等覆盖率≈0 的属性仍不接，见 `docs/research/real-store-data-sources.md` §①）。
