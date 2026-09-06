// 操作者会话：启动时以 GET /api/auth/me 确认；fetch 层广播的 401 失效在这里落地。
// 路由守卫只看 operator/bootstrapping 两个状态，不做二次请求。

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { onAuthExpired } from '../api/client'
import { api } from '../api/endpoints'
import type { Operator } from '../api/types'

interface AuthContextValue {
  operator: Operator | null
  bootstrapping: boolean
  signIn: (operator: Operator) => void
  signOut: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [operator, setOperator] = useState<Operator | null>(null)
  const [bootstrapping, setBootstrapping] = useState(true)

  useEffect(() => {
    let cancelled = false
    api
      .me()
      .then((op) => {
        if (!cancelled) setOperator(op)
      })
      .catch(() => {
        if (!cancelled) setOperator(null)
      })
      .finally(() => {
        if (!cancelled) setBootstrapping(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => onAuthExpired(() => setOperator(null)), [])

  const signIn = useCallback((op: Operator) => setOperator(op), [])
  const signOut = useCallback(() => {
    void api.logout().catch(() => undefined)
    setOperator(null)
  }, [])

  const value = useMemo(
    () => ({ operator, bootstrapping, signIn, signOut }),
    [operator, bootstrapping, signIn, signOut],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === null) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return ctx
}
