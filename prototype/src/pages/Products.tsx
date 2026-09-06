import { Link } from 'react-router-dom'
import { Package, ArrowUp } from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import CitationChip from '../components/CitationChip'
import { useStore } from '../store/store'
import { isSpecDoc } from '../store/selectors'

// 商品页：结构化事实。净含量/保质期只来自已发布资产的写回（ADR 0010），
// 未发布的抽取永远不会出现在这里。

function SpecCell({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <div className="text-xs text-caption">{label}</div>
      {value ? (
        <div className="text-sm font-medium text-ink mt-0.5">{value}</div>
      ) : (
        <div className="text-sm text-caption mt-0.5 italic-none">待写回（无已发布规格）</div>
      )}
    </div>
  )
}

export default function Products() {
  const products = useStore((s) => s.products)
  const assets = useStore((s) => s.assets)

  return (
    <div className="p-4 lg:p-6">
      <PageHeader
        title="中台 · 商品"
        desc="可卖对象的结构化事实。规格字段按商品类目定（食品有保质期，器皿是材质与净含量），只在资产发布时写回；价格与库存走查询接口。"
      />

      <div className="grid md:grid-cols-2 gap-4">
        {products.map((p) => {
          const fullState = { products, assets } as never
          // 找到写回来源：挂在该商品上、有已发布指针的规格文档
          const source = assets.find(
            (a) => a.productId === p.id && a.publishedV != null && isSpecDoc(fullState, a)
          )
          const pending = assets.find(
            (a) => a.productId === p.id && a.state === '待人洗' && isSpecDoc(fullState, a)
          )
          return (
            <div key={p.id} className="panel">
              <div className="flex items-start gap-3 px-4 py-3.5 border-b border-line-1">
                <div className="w-9 h-9 rounded-[6px] bg-fill-100 text-ink-3 flex items-center justify-center shrink-0">
                  <Package size={17} />
                </div>
                <div className="min-w-0">
                  <div className="text-base font-semibold text-ink">{p.name}</div>
                  <div className="font-mono text-xs text-caption tabular-nums mt-0.5">{p.id}</div>
                </div>
                <div className="flex-1" />
                <div className="text-right">
                  <div className="tabular-nums text-lg font-semibold text-ink">{p.stock}</div>
                  <div className="text-xs text-caption">库存（查询接口）</div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 px-4 py-3.5">
                {p.specSchema.map((k) => (
                  <SpecCell key={k} label={k} value={p.specs[k] ?? null} />
                ))}
              </div>

              <div className="px-4 pb-3.5">
                <div className="text-xs text-caption mb-1.5">规格写回来源</div>
                {source ? (
                  <div className="flex items-center gap-2">
                    <CitationChip assetId={source.id} v={source.publishedV!} />
                    <span className="text-xs text-caption">发布时写回</span>
                  </div>
                ) : (
                  <div className="text-xs text-caption">尚无已发布规格文档写回。</div>
                )}
                {pending && (
                  <div className="mt-2 flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-[6px] px-2.5 py-1.5">
                    <ArrowUp size={12} />
                    <Link to={`/platform/assets/${pending.id}`} className="underline">
                      {pending.id}
                    </Link>
                    有待人洗规格；发布前此处保持旧值，不会预览未发布数据。
                  </div>
                )}
              </div>

              <div className="px-4 pb-3.5">
                <div className="text-xs text-caption mb-1.5">卖点</div>
                <div className="flex flex-wrap gap-1.5">
                  {p.sellingPoints.map((sp) => (
                    <span
                      key={sp}
                      className="inline-flex h-6 items-center px-2 text-xs text-ink-2 bg-fill-60 border border-line-2 rounded-[4px]"
                    >
                      {sp}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
