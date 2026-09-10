// 操作者仪表上下文（第 43 刀修正）：AppShell 取一次 stats，侧栏角标与总览页
// 共用同一份数据——一个来源，不会两处漂移，也避免同一路由每次导航请求两次。
// 刷新语义仍归 AppShell：fetcher 稳定 + pathname 作 reloadKey，无定时器。
// 本文件不导出组件（裸 Context + hook），Provider 由 AppShell 直接用。

import { createContext, useContext } from 'react'
import type { StatsOverview } from '../api/types'
import type { ApiData } from '../hooks/useApiData'

export interface StatsValue {
  state: ApiData<StatsOverview>
  reload: () => void
}

// 默认值=loading 空态：未包 Provider 的树（理论上不存在）不会崩
export const StatsContext = createContext<StatsValue>({
  state: { phase: 'loading' },
  reload: () => {},
})

export function useStats(): StatsValue {
  return useContext(StatsContext)
}
