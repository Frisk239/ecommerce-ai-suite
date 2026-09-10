import { useEffect } from 'react'

/**
 * Esc 关闭浮层（抽屉/弹层的统一口径）。
 *
 * 为什么要有这个 hook：Esc 关闭此前是逐处手写，结果五个抽屉里三个漏了监听
 * （商品编辑 / 素材任务 / 考核作答），同一个产品里 Esc 时灵时不灵。新增浮层
 * 一律用本 hook，别再各处抄。
 *
 * @param active  浮层是否打开（关闭时不挂监听）
 * @param onClose 关闭动作
 * @param enabled false 时不响应（提交中等不可中断状态，与遮罩点击同一门禁）
 */
export function useEscapeClose(active: boolean, onClose: () => void, enabled = true) {
  useEffect(() => {
    if (!active || !enabled) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, enabled, onClose])
}
