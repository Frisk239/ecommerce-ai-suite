import { useState } from 'react'
import { Link } from 'react-router-dom'
import { GearSix, PencilSimple, TrashSimple, CheckCircle, Plus } from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import Confirm from '../components/Confirm'
import { dispatch, useStore } from '../store/store'
import type { ModelConfig } from '../store/types'

// 模型配置：操作者在这里管理客服可用的底座（基座 / 微调）、接入地址与推理参数。
// 「设为客服底座」与客服页的基座/微调开关读同一份激活状态。

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
        {model.type === 'finetuned' ? (
          <span className="badge-accent">微调</span>
        ) : (
          <span className="badge-neutral">基座</span>
        )}
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
        {!active && model.type !== 'base' && (
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

      {model.source && (
        <div className="mt-3 text-xs text-ink-3 bg-fill-60 border border-line-2 rounded-[6px] px-2.5 py-1.5">
          血缘：{model.source}
        </div>
      )}

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
        body="删除后客服不可再切换到这个底座；导出血缘记录保留。"
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
    type: 'base' as 'base' | 'finetuned',
    provider: '自托管 vLLM',
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
        type: form.type,
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
            placeholder="如：通用基座 · glm-4.7-air"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </label>
        <label className="block">
          <span className="text-xs text-ink-3">类型</span>
          <select
            className="input w-full mt-1"
            value={form.type}
            onChange={(e) => setForm({ ...form, type: e.target.value as 'base' | 'finetuned' })}
          >
            <option value="base">基座</option>
            <option value="finetuned">微调</option>
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-ink-3">提供方 / 部署方式</span>
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
        desc="管理客服可用的底座模型：接入地址、推理参数、启用哪个。微调底座来自「模型微调」的导出训练产物；客服页的基座/微调开关切换的就是这里激活的底座。"
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
