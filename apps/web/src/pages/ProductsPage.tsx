// 商品页：结构化事实。规格字段按类目 schema 数据驱动；值只在资产发布时写回，
// 来源芯片 A-{id} · v{N} 指向写回它的那版资产（未写回显示 —，绝不预览未发布数据）。

import { useCallback } from 'react'
import { Package, Warning } from '@phosphor-icons/react'
import { api } from '../api/endpoints'
import { useApiData } from '../hooks/useApiData'
import CitationChip from '../components/CitationChip'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'
import { ErrorBanner } from '../components/Banner'

export default function ProductsPage() {
  const fetcher = useCallback(() => api.listProducts(), [])
  const { state, reload } = useApiData(fetcher)
  const products = state.phase === 'ok' ? state.data : []

  return (
    <div>
      <PageHeader
        title="中台 · 商品"
        desc="可卖对象的结构化事实。规格字段按类目定（食品有保质期，器皿是材质与净含量），只在资产发布时写回；未写回的字段显示 —。"
      />

      {state.phase === 'loading' ? (
        <div className="panel">
          <SkeletonRows rows={8} />
        </div>
      ) : state.phase === 'error' ? (
        <>
          <ErrorBanner error={state.error} onRetry={reload} />
          <div className="panel">
            <Empty icon={<Warning aria-hidden size={26} />} title="商品加载失败" hint="上面的横幅可重试。" />
          </div>
        </>
      ) : products.length === 0 ? (
        <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
          <Empty
            icon={<Package aria-hidden size={24} />}
            title="还没有商品"
            hint="商品由中台维护；当前店铺尚未灌入任何商品。"
          />
        </div>
      ) : (
        <div className="grid items-start gap-4 md:grid-cols-2">
          {products.map((product) => (
            <div key={product.id} className="panel panel-hover hover:-translate-y-0.5">
              <div className="flex items-center gap-3 border-b border-line-2 px-4 py-3.5">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[6px] bg-accent-soft text-accent-strong">
                  <Package aria-hidden size={17} />
                </span>
                <div className="min-w-0">
                  <div className="text-[15px] font-semibold leading-6 text-ink">{product.name}</div>
                  <div className="mt-0.5 flex items-center gap-2">
                    <span className="kind-chip">{product.category}</span>
                    <span className="font-mono text-xs text-ink-3">P-{product.id}</span>
                  </div>
                </div>
              </div>

              <table className="table-gov">
                <thead>
                  <tr>
                    <th className="w-24">规格字段</th>
                    <th>当前值</th>
                    <th className="w-32">写回来源</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(product.spec_schema).map(([field, rule]) => {
                    const entry = product.spec_values[field]
                    return (
                      <tr key={field}>
                        <td className="text-ink-2">
                          {field}
                          {rule.required === true ? <span className="ml-0.5 text-danger">*</span> : null}
                        </td>
                        <td className={entry ? 'font-medium text-ink' : 'text-ink-3'}>
                          {entry ? entry.value : '—'}
                        </td>
                        <td>
                          {entry ? (
                            <CitationChip assetId={entry.source.asset_id} version={entry.source.version} />
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>

              <div className="border-t border-line-1 px-4 py-2.5 text-xs leading-5 text-ink-3">
                写回只在资产发布时发生；待人洗的抽取不会出现在这里。
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
