import { type RefObject, useEffect } from 'react'

import { useLatest } from './useLatest'

/**
 * O ponteiro caiu na barra de rolagem do próprio elemento (fora da área de conteúdo dele)?
 * Arrastar a barra de rolagem não é um "clique fora" (R16).
 */
export function isOnScrollbar(event: Pick<PointerEvent, 'clientX' | 'clientY'>, target: Element): boolean {
  if (!(target instanceof HTMLElement)) return false
  const scrollsY = target.scrollHeight > target.clientHeight
  const scrollsX = target.scrollWidth > target.clientWidth
  if (!scrollsY && !scrollsX) return false
  const rect = target.getBoundingClientRect()
  const x = event.clientX - rect.left - target.clientLeft
  const y = event.clientY - rect.top - target.clientTop
  return (scrollsY && x >= target.clientWidth) || (scrollsX && y >= target.clientHeight)
}

/**
 * Fecha um painel flutuante com Esc ou clique fora (ignorando os elementos em `ignore`, a barra de
 * rolagem de qualquer elemento e o que `shouldDismiss` recusar).
 */
export function useDismiss(
  ref: RefObject<HTMLElement | null>,
  onDismiss: () => void,
  {
    enabled = true,
    escape = true,
    ignore = [] as RefObject<HTMLElement | null>[],
    shouldDismiss,
  }: {
    enabled?: boolean
    escape?: boolean
    ignore?: RefObject<HTMLElement | null>[]
    shouldDismiss?: (event: PointerEvent) => boolean
  } = {},
): void {
  const callback = useLatest(onDismiss)
  const ignoreRef = useLatest(ignore)
  const shouldDismissRef = useLatest(shouldDismiss)

  useEffect(() => {
    if (!enabled) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (!target || ref.current?.contains(target)) return
      // Avisos (toasts) e outros painéis marcados não fecham o painel atual.
      if (target instanceof Element && target.closest('[data-dismiss-ignore]')) return
      if (ignoreRef.current.some((r) => r.current?.contains(target))) return
      if (target instanceof Element && isOnScrollbar(event, target)) return
      if (shouldDismissRef.current && !shouldDismissRef.current(event)) return
      callback.current()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (escape && event.key === 'Escape' && !event.defaultPrevented) {
        event.preventDefault()
        event.stopPropagation()
        callback.current()
      }
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [ref, enabled, escape, callback, ignoreRef, shouldDismissRef])
}
