// 操作失败的内联错误条（第 25 刀收口）：统一此前散在 7 个文件里手写的
// role="alert" 红条 DOM。与 ErrorBanner 的分工——ErrorBanner 面向 API 错误对象
// （unknown + detailText + 可挂重试），本组件面向「文案已成形」的操作反馈
// （调用方拼好的 detailText 串或本地校验语），不带重试、体量更紧凑。
// 三档视觉对照旧内联形状逐一冻结；className 只承载落点相关的外边距（mb-4 等）。

import { Warning } from '@phosphor-icons/react'

const RED_EDGE = 'border border-[rgba(180,35,24,0.22)] bg-[rgba(180,35,24,0.05)] text-danger'

const VARIANTS = {
  /** 抽屉/表单内紧凑条：无图标（考核作答、素材、注册抽屉、编排页）。 */
  plain: `rounded-[6px] px-3 py-2 text-xs leading-5 ${RED_EDGE}`,
  /** 登录卡片内：小图标 + 紧凑字号。 */
  compact: `flex items-center gap-2 rounded-[6px] px-3 py-2 text-xs leading-5 ${RED_EDGE}`,
  /** 列表页页级：大图标 + 正文字号（AssetsListPage/ClipsPage）。 */
  prominent: `flex items-center gap-2.5 rounded-[8px] px-3.5 py-2.5 text-[13px] leading-5 ${RED_EDGE}`,
} as const

export default function ActionError({
  message,
  variant = 'plain',
  className = '',
}: {
  message: string
  variant?: keyof typeof VARIANTS
  /** 外边距等落点样式（如 mb-4/mt-4）。 */
  className?: string
}) {
  return (
    <div role="alert" className={`${VARIANTS[variant]} ${className}`}>
      {variant === 'plain' ? null : (
        <Warning aria-hidden size={variant === 'prominent' ? 15 : 13} className="shrink-0" />
      )}
      {message}
    </div>
  )
}
