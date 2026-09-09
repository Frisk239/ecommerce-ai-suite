# scripts/realdata —— 多来源真实数据灌入（数据刀 I+II）

零产品代码改动：只经既有通道灌真实演示数据。四源均为公开数据，脚本在线拉取，
仓库只入脚本与 50 行样本 fixture（`samples/`，离线单测用）；下载物与生成物落
`out/`（已 gitignore，不入库）。

## 数据源与许可

| 来源 | 内容 | 许可 | 出处 |
|---|---|---|---|
| Wikidata SPARQL | 商品类条目（按类目 QID 采样，中文标签优先） | **CC0**（最宽松，无需额外授权） | <https://www.wikidata.org> · 端点 <https://query.wikidata.org/sparql> · [数据库下载/许可页](https://www.wikidata.org/wiki/Wikidata:Database_download) |
| online_shopping_10_cats | 6.2 万条中文电商评论，10 类目，正/负情感标注（cat/label/review） | GitHub 开放语料库，**研究用途**：演示请保留出处链接，不作商业用途 | [ChineseNlpCorpus](https://github.com/SophonPlus/ChineseNlpCorpus) · [数据集说明](https://github.com/SophonPlus/ChineseNlpCorpus/blob/master/datasets/online_shopping_10_cats/intro.ipynb) · zip 直下（脚本内置同 URL） |
| ABCD | 1 万+ 英文人机客服对话（train/dev/test，convo_id/scenario/轮次） | **MIT** | [asappresearch/abcd](https://github.com/asappresearch/abcd) · gzip 直下（脚本内置同 URL；v1.2 路径已 404，现物 v1.1） |
| WANDS | 480 query × 43K 商品 × 233K 三档相关性标注（Exact/Partial/Irrelevant） | **MIT**（保留 LICENSE+引用） | [wayfair/WANDS](https://github.com/wayfair/WANDS) · 三件 csv 直下（脚本内置同 URL；实为 TSV） |

网络：标准库 urllib 自动 respect `HTTPS_PROXY` / `HTTP_PROXY` 环境变量（宿主代理
`http://127.0.0.1:7890` 时直接可用）。Wikidata 查询服务当前激进限速约 1 请求/分钟：
脚本带 User-Agent 礼貌头，429 按 `Retry-After`（缺省 65s）退避重试，实跑一次
全链拉取约 1-2 分钟属正常。

## 通道映射（与六来源通道的关系）

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

# 1. 商品：SPARQL 拉取 → out/products.csv（--load 同时直连灌库，幂等可重跑）
uv run python scripts/realdata/fetch_wikidata_products.py
uv run python scripts/realdata/fetch_wikidata_products.py --load \
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
| `load_reviews.py --src/--zip-file/--cache` | zip 下载地址覆盖 / 本地 zip 直读 / 缓存路径 |
| `load_reviews.py --n/--seed/--import/--publish/--api/--user/--pass` | 抽样条数（默认 2000）/种子 / 登录批量导入 / 发布 N 条（须与 --import 同跑）/ API 基址（默认 `http://localhost:8000`）/ 操作者凭证（默认 operator/operator123，开发种子） |
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

## 测试

`apps/api/tests/test_realdata_scripts.py`（27 例，离线）：转换纯函数 + `samples/`
fixture 断言，含交叉验证——脚本产的批字节能被既有 `parse_import_csv` 直接受理；
ABCD 转写与回流端点同构、WANDS 候选形状与模型列宽对齐。网络/DB 路径不在单测
范围（实跑即验）。

## 边界（后续刀再动）

JDDC（注册门槛）、评论类目→商品挂接、WANDS 视频本体与真时间码、Open Food
Facts 连接层登记、直播数据集（观察项）、定期同步。
