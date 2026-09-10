// 工作队列口径（UX-A1）：CI 冒烟与证据探针是「连接层登记」来源、标题机器可辨的行。
// 两条同时命中才算探针——连接层 MCP 正式登记是正常功能，不能凡 mcp_registered 一律隐藏。
// 单一来源：资产列表（tab 计数 / 列表 / 摘要卡）与总览页计数共用本模块，禁止各处复制谓词。

import type { AssetListItem } from './api/types'

/** 探针行标题前缀：CI 冒烟 mcp-smoke 与证据 probe。 */
const PROBE_TITLE_RE = /^(mcp-smoke|evidence probe)/i

/** 工作队列要排除的探针行：来源为连接层登记，且标题命中探针前缀。 */
export function isWorkProbe(asset: AssetListItem): boolean {
  return asset.source_kind === 'mcp_registered' && PROBE_TITLE_RE.test(asset.title ?? '')
}

/** 工作队列资产：滤掉探针行；计数与列表都必须先过这里再分叉。 */
export function filterWorkAssets(assets: readonly AssetListItem[]): AssetListItem[] {
  return assets.filter((asset) => !isWorkProbe(asset))
}

/** ?view= 取值：work=工作队列（默认视角），all=不过滤。 */
export type AssetView = 'work' | 'all'

/** 缺省 / 非法值回落 work（工作队列是默认视角）。 */
export function parseAssetView(value: string | null): AssetView {
  return value === 'all' ? 'all' : 'work'
}

// ---------- 三态口径（单一来源）----------
// 「待人洗」= 待机洗完成的待办，不含修订（修订跟着它的线上资产走）；
// 「已发布」= 有线上指针（含修订中）；「已接入」= 机洗未完成/失败。
// 资产列表的计数与列表过滤、总览页计数都必须调用这里，禁止各处手抄谓词。

/** 待人洗：纯新待办（pending_review 且还没有线上指针）。 */
export function isPendingWash(asset: AssetListItem): boolean {
  return asset.status === 'pending_review' && asset.current_published_version_no === null
}

/** 已发布：有线上指针（修订中同样算，因为线上仍在服务那一版）。 */
export function isPublished(asset: AssetListItem): boolean {
  return asset.current_published_version_no !== null
}

/** 已接入：机洗未完成或失败。 */
export function isIngested(asset: AssetListItem): boolean {
  return asset.status === 'ingested'
}


// ---------- 导入来源（第 59 刀）----------
// 数据集的批量导入（评论 200 / 开放数据集商品与规格）在待人洗队列里占绝大多数，
// 首屏被它们铺满（审计刀 8 起记债、审计刀 9/10/11 三次复核仍是 180+/194）。
// 口径：**数据集导入 ≠ 人工登记**——把导入行折叠到队尾，人工/系统产生的行先铺开。
// 判据用来源词（单一来源），不按标题猜。

/** 数据集导入来源（折叠组）：这三份开放数据集 + 评论语料 + 通用开放数据集。 */
const IMPORTED_SOURCE_KINDS: ReadonlySet<string> = new Set([
  'review_import',
  'open_dataset',
  'wikidata',
  'openfoodfacts',
  'wands',
])

/** 是否是「导入货」（工作队列里折叠到队尾的那批）。 */
export function isImportedSource(asset: AssetListItem): boolean {
  return IMPORTED_SOURCE_KINDS.has(asset.source_kind)
}
