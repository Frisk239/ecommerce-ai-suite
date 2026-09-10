// 连接层演示页（第 23 刀/ADR 0001/0005/0032）：把中台接口以 MCP 工具形式暴露给
// 外部 Agent 的门面。四工具卡（没有 publish——发布只在治理台）+ 检索试玩 +
// 已发布资产取版预览 + mcp.json 配置复制块。
// 试玩走操作者面等价查询（listAssets 客户端过滤，不真发 MCP 协议请求；真 MCP
// 客户端冒烟见 scripts/mcp_smoke.py）。Bearer 只出现占位符，绝不读显真值。

import { useCallback, useMemo, useState } from 'react'
import {
  Check,
  Copy,
  FileDoc,
  MagnifyingGlass,
  PlugsConnected,
  Prohibit,
} from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { AssetListItem } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { formatAssetId, kindLabel } from '../labels'
import CitationChip from '../components/CitationChip'
import { ErrorBanner } from '../components/Banner'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'

const EMPTY_ASSETS: AssetListItem[] = []

interface ToolDef {
  name: string
  sig: string
  label: string
  desc: string
  write: boolean
}

// 语义与参数摘要对照 README「MCP 连接层」表（0032）；没有 publish（0001/0005）
const TOOLS: ToolDef[] = [
  {
    name: 'search_published',
    sig: 'query',
    label: '检索已发布',
    desc: '按关键词检索当前已发布切块（与客服回答同一索引），返回资产 ID · 版本号与命中摘要。',
    write: false,
  },
  {
    name: 'get_asset',
    sig: 'asset_id, version?',
    label: '取资产',
    desc: '取已发布资产正文快照：不传 version = 当前指针版，传 version = 历史已发布版；待人洗/已接入一律拒绝。',
    write: false,
  },
  {
    name: 'register_asset',
    sig: 'content（必填）, title?, product_id?',
    label: '登记',
    desc: '把外部内容登记进中台，来源固定 mcp_registered，落为已接入等待机洗人洗——不能直接发布。',
    write: true,
  },
  {
    name: 'export_published',
    sig: '（无参数）',
    label: '导出已发布集',
    desc: '导出全部当前已发布资产与该版正文全文：是数据包，不是微调集。',
    write: false,
  },
]

// mcp.json 样例（对照 README）：Bearer 恒为占位符，页面不接触真实 token
const MCP_JSON = [
  '{',
  '  "mcpServers": {',
  '    "ecommerce-suite": {',
  '      "url": "http://localhost:8000/mcp/",',
  '      "headers": {',
  '        "Authorization": "Bearer <MCP_BEARER_TOKEN>"',
  '      }',
  '    }',
  '  }',
  '}',
].join('\n')

const PREVIEW_LIMIT = 500

interface VersionBody {
  version_no: number
  text: string
}

export default function ConnectPage() {
  const fetcher = useCallback(() => api.listAssets(), [])
  const { state, reload } = useApiData(fetcher)
  const assets = state.phase === 'ok' ? state.data : EMPTY_ASSETS

  // 可检索口径与治理台「已发布」tab 同源：当前已发布指针非空（含修订中）
  const published = useMemo(
    () =>
      assets
        .filter((a) => a.current_published_version_no !== null)
        .slice()
        .sort((a, b) => a.id - b.id),
    [assets],
  )

  // —— 检索试玩（操作者面等价查询：已发布集 + 标题/种类关键词客户端过滤） ——
  const [q, setQ] = useState('')
  const [searched, setSearched] = useState<string | null>(null)
  const results = useMemo(() => {
    if (searched === null) return []
    const kw = searched.trim().toLowerCase()
    if (kw === '') return []
    return published.filter(
      (a) => (a.title ?? '').toLowerCase().includes(kw) || a.kind.toLowerCase().includes(kw),
    )
  }, [published, searched])

  // —— 取版预览：选已发布资产 → getVersionText（操作者面版本正文端点） ——
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const selected = useMemo(
    () => published.find((a) => a.id === selectedId) ?? null,
    [published, selectedId],
  )
  // 复用 useApiData：fetcher 随选择变化即重取（未选/数据未到 → null 即空态）
  const versionFetcher = useCallback(async (): Promise<VersionBody | null> => {
    if (selected === null || selected.current_published_version_no === null) return null
    const versionNo = selected.current_published_version_no
    const text = await api.getVersionText(selected.id, versionNo)
    return { version_no: versionNo, text }
  }, [selected])
  const versionQ = useApiData(versionFetcher)

  // —— mcp.json 复制 ——
  const [copied, setCopied] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)
  const copyConfig = () => {
    setCopyFailed(false)
    navigator.clipboard
      .writeText(MCP_JSON)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 2000)
      })
      .catch(() => setCopyFailed(true))
  }

  const body = versionQ.state.phase === 'ok' ? versionQ.state.data : null
  const shownText =
    body !== null
      ? body.text.slice(0, PREVIEW_LIMIT) + (body.text.length > PREVIEW_LIMIT ? '…' : '')
      : null

  return (
    <div className="max-w-[960px]">
      <PageHeader
        title="连接层"
        desc="把中台能力以 MCP 工具暴露给外部 Agent。"
      />

      {state.phase === 'error' ? <ErrorBanner error={state.error} onRetry={reload} /> : null}

      {/* 四工具卡 */}
      <div className="mb-4">
        <div className="panel-title">
          <PlugsConnected aria-hidden size={14} />
          对外工具（4）
        </div>
        <div className="panel overflow-hidden rounded-t-none border-t-0">
          <div className="grid gap-px bg-line-1 md:grid-cols-2">
            {TOOLS.map((t) => (
              <div key={t.name} className="bg-surface px-4 py-3.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-accent-strong">{t.name}</span>
                  <span className={`badge ${t.write ? 'badge-review' : 'badge-ingested'}`}>
                    {t.write ? '可写入' : '只读'}
                  </span>
                </div>
                <div className="mt-1 text-[13px] font-medium text-ink">{t.label}</div>
                <div className="mt-0.5 font-mono text-[11px] leading-5 text-caption">
                  ({t.sig})
                </div>
                <p className="mt-1 text-xs leading-5 text-ink-2">{t.desc}</p>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2 border-t border-line-1 bg-fill px-4 py-2.5 text-[13px] text-ink-2">
            <Prohibit aria-hidden size={14} className="shrink-0" />
            工具列表里没有「publish」——发布只在治理台：外部 Agent 可以登记与读取，发布权只在操作者手里。
          </div>
        </div>
      </div>

      <div className="grid items-start gap-4 md:grid-cols-2">
        {/* 演示 · 外部 Agent 检索 */}
        <div className="panel">
          <div className="flex items-center gap-2 border-b border-line-1 px-4 py-2.5">
            <MagnifyingGlass aria-hidden size={14} className="text-caption" />
            <span className="text-sm font-semibold text-ink">演示 · 外部 Agent 检索</span>
          </div>
          <div className="p-3.5">
            <div className="flex gap-2">
              <input
                className="input flex-1"
                placeholder="调用 search_published，如：保温"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && q.trim() !== '' && setSearched(q)}
              />
              <button
                type="button"
                className="btn btn-primary"
                disabled={q.trim() === ''}
                onClick={() => setSearched(q)}
              >
                调用
              </button>
            </div>
            <div className="mt-1.5 text-xs leading-5 text-caption">
              试玩为操作者面等价查询（当前已发布集按标题匹配）；真检索走同一分块索引。
            </div>

            {state.phase === 'loading' ? <SkeletonRows rows={3} /> : null}
            {searched !== null && state.phase === 'ok' ? (
              <div className="mt-3 divide-y divide-line-1 border-t border-line-1">
                {results.length === 0 ? (
                  <div className="py-4 text-sm leading-6 text-ink-3">
                    0 条结果。检索索引里只有已发布资产；未发布内容对外部 Agent 不可见，也不会半透明展示。
                  </div>
                ) : (
                  results.map((a) => (
                    <div key={a.id} className="flex flex-wrap items-center gap-2 py-2.5">
                      <CitationChip
                        assetId={a.id}
                        version={a.current_published_version_no ?? 0}
                      />
                      <span className="min-w-0 truncate text-sm font-medium text-ink">
                        {a.title ?? formatAssetId(a.id)}
                      </span>
                      <span className="text-xs text-caption">{kindLabel(a.kind)}</span>
                    </div>
                  ))
                )}
              </div>
            ) : null}
          </div>
        </div>

        {/* 演示 · 取资产（已发布资产的当前版正文预览） */}
        <div className="panel">
          <div className="flex items-center gap-2 border-b border-line-1 px-4 py-2.5">
            <FileDoc aria-hidden size={14} className="text-caption" />
            <span className="text-sm font-semibold text-ink">演示 · 取资产版本正文</span>
          </div>
          <div className="space-y-2 p-3.5">
            <select
              className="input w-full"
              aria-label="选择要取版的已发布资产"
              value={selectedId ?? ''}
              onChange={(e) =>
                setSelectedId(e.target.value === '' ? null : Number(e.target.value))
              }
            >
              <option value="">选择已发布资产…</option>
              {published.map((a) => (
                <option key={a.id} value={a.id}>
                  {formatAssetId(a.id)} · v{a.current_published_version_no} ——{' '}
                  {a.title ?? kindLabel(a.kind)}
                </option>
              ))}
            </select>
            {published.length === 0 && state.phase === 'ok' ? (
              <div className="text-xs leading-5 text-ink-3">
                当前没有已发布资产：检索与取版对外都返回空——先治理、后接入，顺序不可逆。
              </div>
            ) : null}
            {selected !== null && versionQ.state.phase === 'loading' ? (
              <div className="text-xs text-caption">取版中…</div>
            ) : null}
            {versionQ.state.phase === 'error' ? (
              <div className="rounded-[6px] border border-[rgba(180,35,24,0.22)] bg-[rgba(180,35,24,0.05)] px-2.5 py-2 text-xs leading-5 text-danger">
                {detailText(versionQ.state.error)}
              </div>
            ) : null}
            {shownText !== null ? (
              <div>
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  {selected !== null ? (
                    <CitationChip
                      assetId={selected.id}
                      version={selected.current_published_version_no ?? 0}
                    />
                  ) : null}
                  <span className="text-xs text-caption">
                    正文前 {PREVIEW_LIMIT} 字预览；get_asset 返回该版全文快照
                  </span>
                </div>
                <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-[6px] border border-line-2 bg-fill px-2.5 py-2 font-mono text-xs leading-5 text-ink-2">
                  {shownText}
                </pre>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {/* mcp.json 配置块 */}
      <div className="panel mt-4">
        <div className="flex items-center gap-2 border-b border-line-1 px-4 py-2.5">
          <span className="min-w-0 flex-1 text-sm font-semibold text-ink">
            接入配置 · Cursor mcp.json
          </span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={copyConfig}>
            {copied ? (
              <Check aria-hidden size={13} className="text-ok" />
            ) : (
              <Copy aria-hidden size={13} />
            )}
            {copied ? '已复制' : '复制'}
          </button>
        </div>
        <pre className="overflow-x-auto px-4 py-3 font-mono text-xs leading-6 text-ink-2">
          {MCP_JSON}
        </pre>
        <div className="border-t border-line-1 px-4 py-2.5 text-xs leading-5 text-caption">
          把占位符换成服务端配置的 MCP_BEARER_TOKEN 即接入；真实 token 只存在服务端配置，
          本页与仓库都不出现明文。鉴权与操作者登录会话完全隔离。
          {copyFailed ? (
            <span className="ml-1 text-danger">复制失败：请手动选中代码块复制。</span>
          ) : null}
        </div>
      </div>

      <div className="mt-4 flex items-center gap-1.5 text-xs text-caption">
        <PlugsConnected aria-hidden size={13} />
        新系统接入 = 提示词 + 选这四个工具；内部模块走中台接口互调，不绕连接层。
      </div>
    </div>
  )
}
