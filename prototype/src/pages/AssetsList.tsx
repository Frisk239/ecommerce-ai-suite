import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { MagnifyingGlass, CaretRight, Plus, X } from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import Empty from '../components/Empty'
import { KindBadge, StateBadgeWithRevision } from '../components/StateBadge'
import { dispatch, useStore } from '../store/store'
import type { AssetKind, KnowledgeGap } from '../store/types'

const TABS = ['全部', '待人洗', '已接入', '已发布', '知识缺口'] as const
type ListTab = (typeof TABS)[number]

// 治理台自己的登记入口：供应商文档等直接写入中台，落入已接入
function RegisterForm({ onClose, gap }: { onClose: () => void; gap?: KnowledgeGap }) {
  const products = useStore((s) => s.products)
  const [kind, setKind] = useState<AssetKind>('文档')
  const [title, setTitle] = useState(gap ? `补口径 · ${gap.question}` : '')
  const [content, setContent] = useState('')
  const [productId, setProductId] = useState(gap?.productId ?? '')

  const submit = () => {
    if (!title.trim() || !content.trim()) return
    dispatch({
      type: 'REGISTER_MANUAL',
      kind,
      title: title.trim(),
      content: content.trim(),
      ...(productId ? { productId } : {}),
      ...(gap ? { fillsGapId: gap.id } : {}),
    })
    onClose()
  }

  return (
    <div className="panel p-4 mb-4 space-y-2.5">
      <div className="flex items-center gap-2">
        <div className="text-sm font-semibold text-ink">登记资产</div>
        <span className="text-xs text-caption">
          {gap ? `补缺口 ${gap.id}：登记后进入已接入，发布后缺口关闭` : '登记后进入已接入，机洗完成后到待人洗。来源=上传'}
        </span>
        <span className="flex-1" />
        <button className="btn-ghost btn-sm" onClick={onClose} aria-label="关闭登记表单">
          <X size={13} />
        </button>
      </div>
      <div className="grid sm:grid-cols-3 gap-2">
        <label className="block">
          <span className="text-xs text-ink-3">种类</span>
          <select
            className="input w-full mt-1"
            value={kind}
            onChange={(e) => setKind(e.target.value as AssetKind)}
          >
            {(['文档', '图片', '视频', '对话', '素材'] as AssetKind[]).map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label className="block sm:col-span-2">
          <span className="text-xs text-ink-3">标题</span>
          <input
            className="input w-full mt-1"
            placeholder="如：钛钢保温杯 · 检测报告"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
      </div>
      <label className="block">
        <span className="text-xs text-ink-3">正文 / 转写内容</span>
        <textarea
          className="input w-full min-h-[96px] py-2 mt-1 leading-6"
          placeholder="粘贴文档正文或转写文本；机洗将从此生成工作版本"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-ink-3">
          挂载商品（可选）
          <select className="input h-7 text-xs" value={productId} onChange={(e) => setProductId(e.target.value)}>
            <option value="">不挂载</option>
            {products.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id} {p.name}
              </option>
            ))}
          </select>
        </label>
        <span className="flex-1" />
        <button className="btn-ghost btn-sm" onClick={onClose}>
          取消
        </button>
        <button className="btn-primary btn-sm" onClick={submit} disabled={!title.trim() || !content.trim()}>
          登记到中台
        </button>
      </div>
    </div>
  )
}

export default function AssetsList() {
  const assets = useStore((s) => s.assets)
  const gaps = useStore((s) => s.knowledgeGaps)
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const state = (params.get('state') as ListTab | null) ?? '全部'
  const fillingGapId = params.get('gap')
  const fillingGap = gaps.find((g) => g.id === fillingGapId)
  const [q, setQ] = useState('')
  const [registering, setRegistering] = useState(!!fillingGap)

  // 口径对齐（ADR 0006）：「已发布」tab = 线上在服务（publishedV，含修订中的线上版，
  // 行内徽章显示修订状态）；「待人洗」tab = 纯新待办（修订中的资产不在这里重复出现，
  // 它的待办在已发布 tab 的「待人洗·修订中」徽章上）。总览计数同口径，数字不再对不上。
  const filtered = useMemo(() => {
    if (state === '知识缺口') return []
    return assets.filter((a) => {
      if (state === '已发布' && a.publishedV == null) return false
      if (state === '待人洗' && !(a.state === '待人洗' && a.publishedV == null)) return false
      if (state === '已接入' && a.state !== '已接入') return false
      if (q && !`${a.id} ${a.title}`.toLowerCase().includes(q.toLowerCase())) return false
      return true
    })
  }, [assets, state, q])

  const countOf = (t: ListTab) =>
    t === '全部'
      ? assets.length
      : t === '已发布'
        ? assets.filter((a) => a.publishedV != null).length
        : t === '待人洗'
          ? assets.filter((a) => a.state === '待人洗' && a.publishedV == null).length
          : t === '知识缺口'
            ? gaps.filter((g) => g.status === 'open').length
            : assets.filter((a) => a.state === '已接入').length

  return (
    <div className="p-4 lg:p-6">
      {registering && (
        <RegisterForm
          gap={fillingGap}
          onClose={() => {
            setRegistering(false)
            if (fillingGapId) {
              const next = new URLSearchParams(params)
              next.delete('gap')
              setParams(next)
            }
          }}
        />
      )}
      <PageHeader
        title="中台 · 资产"
        desc="三态：已接入 → 待人洗 → 已发布。知识缺口不是资产：拒答后在这里排队，人补文档再发布。"
        actions={
          <button className="btn-primary" onClick={() => setRegistering(true)}>
            <Plus size={14} />
            登记资产
          </button>
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <div className="seg flex-wrap">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setParams(t === '全部' ? {} : { state: t })}
                className={`seg-tab ${state === t ? 'seg-tab-active' : ''}`}
              >
                {t}
                <span className="tabular-nums ml-1 text-caption">{countOf(t)}</span>
              </button>
            ))}
          </div>
          <div className="flex-1" />
          <div className="relative">
            <MagnifyingGlass
              size={14}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-caption"
            />
            <input
              className="input pl-8 w-52"
              placeholder="搜索资产 ID 或标题"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
        </div>
      </PageHeader>

      {state === '知识缺口' ? (
        <div className="panel overflow-hidden">
          {gaps.length === 0 ? (
            <Empty title="没有知识缺口" hint="客服无证据拒答时会记一条待办。不是工单，也不是资产。" />
          ) : (
            <table className="table-base">
              <thead>
                <tr>
                  <th className="w-24">缺口</th>
                  <th>顾客原问</th>
                  <th className="w-32">商品</th>
                  <th className="w-24">状态</th>
                  <th className="w-36">时间</th>
                  <th className="w-28"></th>
                </tr>
              </thead>
              <tbody>
                {gaps.map((g) => (
                  <tr key={g.id}>
                    <td className="font-mono text-xs tabular-nums">{g.id}</td>
                    <td className="text-sm">{g.question}</td>
                    <td className="font-mono text-xs">{g.productId ?? '—'}</td>
                    <td>
                      {g.status === 'open' ? (
                        <span className="badge-review">待补</span>
                      ) : (
                        <Link to={`/platform/assets/${g.filledAssetId}`} className="badge-published hover:underline">
                          已补 {g.filledAssetId}
                        </Link>
                      )}
                    </td>
                    <td className="text-xs text-caption tabular-nums">{g.createdAt}</td>
                    <td>
                      {g.status === 'open' && (
                        <button
                          className="btn-primary btn-sm"
                          onClick={() => {
                            setParams({ state: '知识缺口', gap: g.id })
                            setRegistering(true)
                          }}
                        >
                          去补文档
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : filtered.length === 0 ? (
        <div className="panel">
          <Empty
            icon={<MagnifyingGlass size={28} />}
            title={q ? '没有匹配的资产' : '这个状态下暂时没有资产'}
            hint={
              q
                ? '换个关键词，或清空搜索看全部资产。'
                : '新内容通过「登记」进入已接入：右上「登记资产」直接录入，或由客服会话回流、素材成品、切片拣选、连接层登记汇入。'
            }
          />
        </div>
      ) : (
        <>
          {/* 桌面表格 */}
          <div className="panel hidden md:block overflow-hidden">
            <table className="table-base">
              <thead>
                <tr>
                  <th className="w-24">资产 ID</th>
                  <th>标题</th>
                  <th className="w-20">种类</th>
                  <th className="w-24">来源</th>
                  <th className="w-44">状态</th>
                  <th className="w-32">挂载商品</th>
                  <th className="w-36">登记时间</th>
                  <th className="w-8"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((a) => (
                  <tr
                    key={a.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/platform/assets/${a.id}`)}
                  >
                    <td className="font-mono text-xs text-ink-3 tabular-nums">{a.id}</td>
                    <td>
                      <Link
                        to={`/platform/assets/${a.id}`}
                        className="text-ink hover:text-accent-strong font-medium"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {a.title}
                      </Link>
                    </td>
                    <td>
                      <KindBadge kind={a.kind} />
                    </td>
                    <td className="text-xs text-ink-3">{a.sourceKind}</td>
                    <td>
                      <StateBadgeWithRevision asset={a} />
                    </td>
                    <td className="font-mono text-xs text-ink-3 tabular-nums">{a.productId ?? '无'}</td>
                    <td className="text-xs text-ink-3 tabular-nums">{a.createdAt}</td>
                    <td className="text-caption">
                      <CaretRight size={14} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 窄屏卡片 */}
          <div className="md:hidden space-y-2">
            {filtered.map((a) => (
              <Link
                key={a.id}
                to={`/platform/assets/${a.id}`}
                className="panel p-3 block active:bg-fill-60"
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-caption tabular-nums">{a.id}</span>
                  <KindBadge kind={a.kind} />
                  <span className="flex-1" />
                  <StateBadgeWithRevision asset={a} />
                </div>
                <div className="mt-1.5 text-sm font-medium text-ink">{a.title}</div>
                <div className="mt-1 text-xs text-caption tabular-nums">
                  {a.sourceKind}
                  {a.productId ? ` · 挂载 ${a.productId}` : ''}
                  {' · '}
                  {a.createdAt}
                </div>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
