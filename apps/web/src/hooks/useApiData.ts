// 通用数据获取 hook：loading / error / ok 三态 + 手动 reload（错误横幅的重试按钮接它）。
// fetcher 必须由调用方 useCallback 稳定，依赖变化即重新请求。

import { useCallback, useEffect, useState } from 'react'
import { ApiError, isApiError } from '../api/client'

export type ApiData<T> =
  | { phase: 'loading' }
  | { phase: 'error'; error: ApiError }
  | { phase: 'ok'; data: T }

export function useApiData<T>(fetcher: () => Promise<T>, reloadKey: unknown = null) {
  const [state, setState] = useState<ApiData<T>>({ phase: 'loading' })
  const [nonce, setNonce] = useState(0)
  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    setState({ phase: 'loading' })
    fetcher()
      .then((data) => {
        if (!cancelled) setState({ phase: 'ok', data })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setState({
          phase: 'error',
          error: isApiError(err) ? err : new ApiError('请求失败，请重试', 0, null),
        })
      })
    return () => {
      cancelled = true
    }
  }, [fetcher, reloadKey, nonce])

  return { state, reload }
}
