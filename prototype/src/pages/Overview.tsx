import { Link } from 'react-router-dom'
import {
  ArrowRight,
  ArrowsClockwise,
  Brain,
  ChatCircleDots,
  CheckCircle,
  Database,
  ImageSquare,
  Tray,
  Package,
} from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import { useStore } from '../store/store'

const LOOPS = [
  {
    to: '/service',
    icon: ChatCircleDots,
    name: '接待闭环',
    line: '顾客提问，客服只引已发布资产回答；会话回流登记，治理后再服务下一位顾客。',
    steps: [
      { label: '治理资产', to: '/platform/assets?state=待人洗' },
      { label: '带引用回答', to: '/service' },
      { label: '回流登记', to: '/service' },
      { label: '再治理发布', to: '/platform/assets?state=已接入' },
    ],
  },
  {
    to: '/materials',
    icon: ImageSquare,
    name: '内容闭环',
    line: '商品知识生成素材、直播切片汇入素材中心，登记进中台后由运营 Agent 组装投放。',
    steps: [
      { label: '素材生成', to: '/materials' },
      { label: '切片汇入', to: '/clips' },
      { label: '登记治理', to: '/platform/assets?state=已接入' },
      { label: '运营投放', to: '/ops' },
    ],
  },
  {
    to: '/finetune',
    icon: Brain,
    name: '能力闭环',
    line: '检索不够好时，从已发布导出微调集，训练注册为底座，把适配后的模型送回客服。',
    steps: [
      { label: '导出微调集', to: '/finetune' },
      { label: '注册底座', to: '/finetune' },
      { label: '客服切换', to: '/service' },
      { label: '考核验证', to: '/coach' },
    ],
  },
]

export default function Overview() {
  // 选择器必须返回稳定引用（数组/原始值），不能每次构造新对象
  const assets = useStore((s) => s.assets)
  const products = useStore((s) => s.products)
  const sessions = useStore((s) => s.sessions)
  const exports = useStore((s) => s.exports)

  const stats = [
    {
      to: '/platform/assets?state=待人洗',
      label: '待人洗队列',
      // 与列表「待人洗」tab 同口径：纯新待办（修订中的资产在「已发布」tab 带徽章）
      value: assets.filter((a) => a.state === '待人洗' && a.publishedV == null).length,
      icon: Tray,
      tone: 'amber' as const,
    },
    {
      to: '/platform/assets?state=已接入',
      label: '已接入',
      value: assets.filter((a) => a.state === '已接入').length,
      icon: Database,
      tone: 'stone' as const,
    },
    {
      to: '/platform/assets?state=已发布',
      // 按可检索口径计数（publishedV）：修订中的资产线上版仍在服务，不该从这行消失
      label: '已发布 · 可被 Agent 检索',
      value: assets.filter((a) => a.publishedV != null).length,
      icon: CheckCircle,
      tone: 'emerald' as const,
    },
    {
      to: '/platform/products',
      label: '商品',
      value: products.length,
      icon: Package,
      tone: 'accent' as const,
    },
  ]

  // 图标块全中性（DSH 风格：色彩只承担状态语义，不装饰）；状态差异由数字下的链接语义承担
  const toneCls = {
    amber: 'bg-fill-60 border-line-2 text-ink-3',
    stone: 'bg-fill-60 border-line-2 text-ink-3',
    emerald: 'bg-fill-60 border-line-2 text-ink-3',
    accent: 'bg-fill-60 border-line-2 text-ink-3',
  }

  return (
    <div className="p-4 lg:p-6 max-w-[1200px]">
      <PageHeader
        title="总览"
        desc="八块能力共用一份被治理过的数据。三条闭环从这里一句点进真实页面，治理队列是每天的入口。"
        actions={
          <div className="flex items-center gap-1.5 text-xs text-ink-3 font-mono tabular-nums">
            <ArrowsClockwise size={13} />
            会话 {sessions.length} · 导出 {exports.length}
          </div>
        }
      />

      {/* 治理队列：统计卡（小标签 + 大数字 + 语义图标）*/}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 mb-4">
        {stats.map((st) => (
          <Link key={st.label} to={st.to} className="stat-card group">
            <div className="flex items-start justify-between">
              <div className="stat-label">{st.label}</div>
              <div
                className={`w-8 h-8 rounded-[6px] border flex items-center justify-center ${toneCls[st.tone]}`}
              >
                <st.icon size={15} weight="regular" />
              </div>
            </div>
            <div className="stat-value mt-2">
              {st.value}
            </div>
          </Link>
        ))}
      </div>

      {/* 三条闭环：步进器（每个节点是真路由），不是三列营销磁贴 */}
      <div className="panel mb-4 divide-y divide-line-1">
        {LOOPS.map((loop) => (
          <div key={loop.name} className="flex flex-col md:flex-row md:items-center gap-3 md:gap-5 px-4 py-4">
            <div className="flex items-center gap-2.5 md:w-44 shrink-0">
              <div className="w-9 h-9 rounded-[6px] bg-fill-60 border border-line-2 text-ink-2 flex items-center justify-center shrink-0">
                <loop.icon size={17} weight="regular" />
              </div>
              <div>
                <Link
                  to={loop.to}
                  className="text-[15px] font-semibold text-ink tracking-tight hover:text-accent-strong transition-colors"
                >
                  {loop.name}
                </Link>
                <p className="text-xs text-ink-3 leading-5 mt-0.5 md:hidden">{loop.line}</p>
              </div>
            </div>
            <p className="hidden md:block text-[13px] text-ink-3 leading-6 md:w-72 shrink-0">
              {loop.line}
            </p>
            {/* 步进器：编号节点 + 连接线，全部可点 */}
            <div className="flex items-center flex-wrap gap-y-2 flex-1 min-w-0">
              {loop.steps.map((st, i) => (
                <div key={st.label} className="flex items-center">
                  {i > 0 && <div className="w-5 h-px bg-line-3 mx-0.5" />}
                  <Link
                    to={st.to}
                    className="group/step flex items-center gap-1.5 px-1.5 py-1 rounded-[6px] hover:bg-fill-60 transition-colors"
                  >
                    <span className="w-5 h-5 rounded-full bg-fill-100 text-ink-3 text-[11px] font-mono tabular-nums flex items-center justify-center group-hover/step:bg-accent group-hover/step:text-white transition-colors">
                      {i + 1}
                    </span>
                    <span className="text-[13px] text-ink-2 group-hover/step:text-ink transition-colors whitespace-nowrap">
                      {st.label}
                    </span>
                  </Link>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* 其余能力：一行的入口，不是营销磁贴 */}
      <div className="panel divide-y divide-line-1">
        {[
          {
            to: '/platform/assets',
            label: '中台 · 资产',
            line: '登记、机洗、人洗、发布、修订；发布权只在这里',
          },
          {
            to: '/platform/products',
            label: '中台 · 商品',
            line: '结构化事实；规格来自已发布资产的写回',
          },
          {
            to: '/coach',
            label: '销售考核',
            line: '从已发布对话资产抽场景，AI 扮演顾客打分',
          },
          {
            to: '/connect',
            label: '连接层（MCP）',
            line: '把中台能力暴露给外部 Agent：检索、取资产、登记、导出，没有发布',
          },
        ].map((row) => (
          <Link
            key={row.to}
            to={row.to}
            className="flex items-center gap-3 px-4 py-3 hover:bg-fill-60/70 transition-colors group"
          >
            <span className="text-sm font-medium text-ink group-hover:text-accent-strong transition-colors">
              {row.label}
            </span>
            <span className="text-xs text-ink-3 truncate">{row.line}</span>
            <ArrowRight
              size={14}
              className="ml-auto text-caption group-hover:text-accent-strong shrink-0 transition-colors"
            />
          </Link>
        ))}
      </div>
    </div>
  )
}
