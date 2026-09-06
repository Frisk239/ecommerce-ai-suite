import { useState } from 'react'
import { Link } from 'react-router-dom'
import { GearSix, PencilSimple, TrashSimple, CheckCircle, Plus } from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import Confirm from '../components/Confirm'
import { dispatch, useStore } from '../store/store'
import type { ModelConfig } from '../store/types'

// 模型配置：厂商 Chat API 接入。不训练、不切微调底座（ADR 0028）。

function ParamRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-ink-3">
      <span className="w-16 text-caption">{k}</span>
      <span className="font-mono tabular-nums text-ink-2">{v}</span>
    </div>
  )
}

function ModelCard({ model, active }: { model: ModelConfig; active: boolean }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(model)
  const [confirming, setConfirming] = useState(false)

  const save = () => {
    dispatch({
      type: 'UPDATE_MODEL',
      id: model.id,
      patch: {
        name: draft.name.trim() || model.name,
        endpoint: draft.endpoint.trim() || model.endpoint,
        params: {
          temperature: Number(draft.params.temperature) || 0.3,
          maxTokens: Number(draft.params.maxTokens) || 1024,
        },
      },
    })
    setEditing(false)
  }

  return (
    <div className={`panel p-4 ${active ? 'border-accent-border ring-1 ring-accent-border/60' : ''}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[15px] font-semibold text-ink tracking-tight">
          {model.name}
        </span>
        <span className="badge-neutral">{model.provider}</span>
        {active && (
          <span className="badge-published">
            <CheckCircle size={11} weight="fill" />
            客服当前使用
          </span>
        )}
        <span className="flex-1" />
        {!active && (
          <button
            className="btn-ghost btn-sm"
            onClick={() => dispatch({ type: 'SET_ACTIVE_MODEL', id: model.id })}
          >
            设为客服底座
          </button>
        )}
        <button
          className="btn-ghost btn-sm"
          onClick={() => {
            setDraft(model)
            setEditing((e) => !e)
          }}
          title="编辑名称、接入地址与参数"
        >
          <PencilSimple size={13} />
          编辑
        </button>
        {!active && (
          <button className="btn-danger btn-sm" onClick={() => setConfirming(true)}>
            <TrashSimple size={13} />
            删除
          </button>
        )}
      </div>

      <div className="mt-3 grid sm:grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <ParamRow k="接入地址" v={model.endpoint} />
          <ParamRow k="提供方" v={model.provider} />
        </div>
        <div className="space-y-1.5">
          <ParamRow k="temperature" v={String(model.params.temperature)} />
          <ParamRow k="max tokens" v={String(model.params.maxTokens)} />
        </div>
      </div>

      {editing && (
        <div className="mt-3 pt-3 border-t border-line-1 space-y-2">
          <div className="grid sm:grid-cols-2 gap-2">
            <label className="block">
              <span className="text-xs text-ink-3">名称</span>
              <input
                className="input w-full mt-1"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="text-xs text-ink-3">接入地址</span>
              <input
                className="input w-full mt-1 font-mono text-xs"
                value={draft.endpoint}
                onChange={(e) => setDraft({ ...draft, endpoint: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="text-xs text-ink-3">temperature</span>
              <input
                className="input w-full mt-1 font-mono tabular-nums"
                value={draft.params.temperature}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    params: { ...draft.params, temperature: Number(e.target.value) || 0 },
                  })
                }
              />
            </label>
            <label className="block">
              <span className="text-xs text-ink-3">max tokens</span>
              <input
                className="input w-full mt-1 font-mono tabular-nums"
                value={draft.params.maxTokens}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    params: { ...draft.params, maxTokens: Number(e.target.value) || 0 },
                  })
                }
              />
            </label>
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn-ghost btn-sm" onClick={() => setEditing(false)}>
              取消
            </button>
            <button className="btn-primary btn-sm" onClick={save}>
              保存
            </button>
          </div>
        </div>
      )}

      <Confirm
        open={confirming}
        title={`删除底座「${model.name}」？`}
        body="删除后客服不再使用这个接入。至少保留一个厂商模型。"
        confirmLabel="确认删除"
        danger
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          dispatch({ type: 'REMOVE_MODEL', id: model.id })
          setConfirming(false)
        }}
      />
    </div>
  )
}

function AddModelForm() {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({
    name: '',
    provider: 'xAI',
    endpoint: '',
    temperature: 0.3,
    maxTokens: 1024,
  })

  const submit = () => {
    if (!form.name.trim() || !form.endpoint.trim()) return
    dispatch({
      type: 'ADD_MODEL',
      input: {
        name: form.name.trim(),
        provider: form.provider.trim() || '未标注',
        endpoint: form.endpoint.trim(),
        params: { temperature: form.temperature, maxTokens: form.maxTokens },
      },
    })
    setForm({ ...form, name: '', endpoint: '' })
    setOpen(false)
  }

  if (!open) {
    return (
      <button className="btn-ghost" onClick={() => setOpen(true)}>
        <Plus size={14} />
        新增自定义底座
      </button>
    )
  }

  return (
    <div className="panel p-4 space-y-3">
      <div className="text-sm font-semibold text-ink">新增自定义底座</div>
      <div className="grid sm:grid-cols-2 gap-2">
        <label className="block">
          <span className="text-xs text-ink-3">名称</span>
          <input
            className="input w-full mt-1"
            placeholder="如：grok-4"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </label>
        <label className="block">
          <span className="text-xs text-ink-3">提供方</span>
          <input
            className="input w-full mt-1"
            value={form.provider}
            onChange={(e) => setForm({ ...form, provider: e.target.value })}
          />
        </label>
        <label className="block">
          <span className="text-xs text-ink-3">接入地址</span>
          <input
            className="input w-full mt-1 font-mono text-xs"
            placeholder="http://host:port/v1"
            value={form.endpoint}
            onChange={(e) => setForm({ ...form, endpoint: e.target.value })}
          />
        </label>
        <label className="block">
          <span className="text-xs text-ink-3">temperature</span>
          <input
            className="input w-full mt-1 font-mono tabular-nums"
            value={form.temperature}
            onChange={(e) =>
              setForm({ ...form, temperature: Number(e.target.value) || 0 })
            }
          />
        </label>
        <label className="block">
          <span className="text-xs text-ink-3">max tokens</span>
          <input
            className="input w-full mt-1 font-mono tabular-nums"
            value={form.maxTokens}
            onChange={(e) => setForm({ ...form, maxTokens: Number(e.target.value) || 0 })}
          />
        </label>
      </div>
      <div className="flex justify-end gap-2">
        <button className="btn-ghost" onClick={() => setOpen(false)}>
          取消
        </button>
        <button className="btn-primary" onClick={submit} disabled={!form.name.trim() || !form.endpoint.trim()}>
          添加
        </button>
      </div>
    </div>
  )
}

export default function Models() {
  const models = useStore((s) => s.models)
  const activeModelId = useStore((s) => s.activeModelId)

  return (
    <div className="p-4 lg:p-6 max-w-[960px]">
      <PageHeader
        title="模型配置"
        desc="厂商 Chat API 接入。本产品不训练模型、不切微调底座。客服推理走这里激活的接入。"
        actions={
          <Link to="/service" className="btn-ghost">
            去 AI 客服试用
          </Link>
        }
      />

      <div className="space-y-3">
        {models.map((m) => (
          <ModelCard key={m.id} model={m} active={m.id === activeModelId} />
        ))}
      </div>

      <div className="mt-4">
        <AddModelForm />
      </div>

      <div className="mt-4 flex items-center gap-1.5 text-xs text-ink-3">
        <GearSix size={13} className="text-caption" />
        原型不真实调用模型；接入地址与参数只做配置演示。工程版这里对应中台接口的模型注册表。
      </div>
    </div>
  )
}
