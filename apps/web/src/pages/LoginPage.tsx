// 登录页：错误文案统一「用户名或密码错误」，不区分账号/密码哪个错（与 API 口径一致）。
// 已登录（含刚登录成功）按来源路径进入，无来源则进资产列表。

import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Database, Warning } from '@phosphor-icons/react'
import { detailText, isApiError } from '../api/client'
import { api } from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'

export default function LoginPage() {
  const { operator, bootstrapping, signIn } = useAuth()
  const location = useLocation()
  const from =
    (location.state as { from?: string } | null)?.from && (location.state as { from: string }).from !== '/login'
      ? (location.state as { from: string }).from
      : '/platform/assets'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!bootstrapping && operator) return <Navigate to={from} replace />

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const op = await api.login(username.trim(), password)
      signIn(op)
    } catch (err) {
      setError(isApiError(err) && err.status === 401 ? '用户名或密码错误' : detailText(err))
      setSubmitting(false)
      return
    }
    setSubmitting(false)
  }

  return (
    <div className="login-page flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <span className="brand-mark flex h-8 w-8 items-center justify-center rounded-[8px]">
            <Database aria-hidden size={16} weight="bold" className="text-white" />
          </span>
          <span className="text-[17px] font-semibold tracking-tight text-ink">电商 AI 套件</span>
          <span className="text-xs text-ink-3">治理台</span>
        </div>

        <form className="panel p-6" onSubmit={submit} noValidate>
          <h1 className="text-[15px] font-semibold text-ink">操作者登录</h1>
          <p className="mt-1 text-xs leading-5 text-ink-3">
            登记、人洗与发布只属于已登录的操作者。
          </p>

          <div className="mt-5 space-y-4">
            <label className="block">
              <span className="field-label">账号</span>
              <input
                className="input"
                name="username"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="field-label">密码</span>
              <input
                className="input"
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          </div>

          {error ? (
            <div
              role="alert"
              className="mt-4 flex items-center gap-2 rounded-[6px] border border-[rgba(180,35,24,0.22)] bg-[rgba(180,35,24,0.05)] px-3 py-2 text-xs leading-5 text-danger"
            >
              <Warning aria-hidden size={13} className="shrink-0" />
              {error}
            </div>
          ) : null}

          <button type="submit" className="btn btn-primary mt-5 w-full" disabled={submitting}>
            {submitting ? '登录中…' : '登录'}
          </button>
        </form>

        <p className="mt-4 text-center text-[11px] leading-4 text-caption">
          本地开发环境 · 凭证见仓库 .env.example（生产必换）
        </p>
      </div>
    </div>
  )
}
