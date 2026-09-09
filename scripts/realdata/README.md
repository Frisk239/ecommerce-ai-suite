# scripts/realdata —— 多来源真实数据灌入（数据刀 I）

零产品代码改动：只经既有通道灌真实演示数据。两源均为公开数据，脚本在线拉取，
仓库只入脚本与 50 行样本 fixture（`samples/`，离线单测用）；下载物与生成物落
`out/`（已 gitignore，不入库）。

## 数据源与许可

| 来源 | 内容 | 许可 | 出处 |
|---|---|---|---|
| Wikidata SPARQL | 商品类条目（按类目 QID 采样，中文标签优先） | **CC0**（最宽松，无需额外授权） | <https://www.wikidata.org> · 端点 <https://query.wikidata.org/sparql> · [数据库下载/许可页](https://www.wikidata.org/wiki/Wikidata:Database_download) |
| online_shopping_10_cats | 6.2 万条中文电商评论，10 类目，正/负情感标注（cat/label/review） | GitHub 开放语料库，**研究用途**：演示请保留出处链接，不作商业用途 | [ChineseNlpCorpus](https://github.com/SophonPlus/ChineseNlpCorpus) · [数据集说明](https://github.com/SophonPlus/ChineseNlpCorpus/blob/master/datasets/online_shopping_10_cats/intro.ipynb) · zip 直下（脚本内置同 URL） |

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
```

| 脚本参数 | 说明 |
|---|---|
| `fetch_wikidata_products.py --limit/--per-category/--seed/--out` | 总行数上限（默认 200）/每类上限（40）/stock 种子（42）/CSV 路径 |
| `load_reviews.py --src/--zip-file/--cache` | zip 下载地址覆盖 / 本地 zip 直读 / 缓存路径 |
| `load_reviews.py --n/--seed/--import/--publish/--api/--user/--pass` | 抽样条数（默认 2000）/种子 / 登录批量导入 / 发布 N 条（须与 --import 同跑）/ API 基址（默认 `http://localhost:8000`）/ 操作者凭证（默认 operator/operator123，开发种子） |

转换落点：商品 → products 行（spec_schema/spec_values 空 JSONB、stock 0-99 固定种子）；
评论 → `(title, content)`，title = `{类目}评论 · {前 18 字}`，content = 评论全文。

## 测试

`apps/api/tests/test_realdata_scripts.py`（16 例，离线）：转换纯函数 + `samples/`
fixture 断言，含交叉验证——脚本产的批字节能被既有 `parse_import_csv` 直接受理。
网络/DB 路径不在单测范围（实跑即验）。

## 边界（数据刀 II 再动）

WANDS / JDDC / ABCD、Open Food Facts 连接层登记、评论类目→商品挂接、定期同步。
