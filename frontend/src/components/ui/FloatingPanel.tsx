import {
  type CSSProperties,
  type KeyboardEventHandler,
  type ReactNode,
  type Ref,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'
import { createPortal } from 'react-dom'

import { cn } from '../../lib/cn'
import type { AnchorRect } from '../../store/ui'

export type Placement = 'right' | 'left' | 'bottom'

const GAP = 8
const MARGIN = 10

/** Posição de um painel ao lado da âncora, sempre dentro da janela. */
export function placePanel(
  anchor: AnchorRect,
  panel: { width: number; height: number },
  viewport: { width: number; height: number },
  preferred: Placement = 'right',
): { left: number; top: number } {
  const fitsRight = anchor.left + anchor.width + GAP + panel.width <= viewport.width - MARGIN
  const fitsLeft = anchor.left - GAP - panel.width >= MARGIN
  let left: number
  let top: number
  if (preferred !== 'bottom' && (fitsRight || fitsLeft)) {
    const useRight = preferred === 'right' ? fitsRight : !fitsLeft
    left = useRight ? anchor.left + anchor.width + GAP : anchor.left - GAP - panel.width
    top = anchor.top
  } else {
    left = anchor.left + anchor.width / 2 - panel.width / 2
    top = anchor.top + anchor.height + GAP
    if (top + panel.height > viewport.height - MARGIN) top = anchor.top - GAP - panel.height
  }
  left = Math.min(Math.max(left, MARGIN), Math.max(MARGIN, viewport.width - MARGIN - panel.width))
  top = Math.min(Math.max(top, MARGIN), Math.max(MARGIN, viewport.height - MARGIN - panel.height))
  return { left, top }
}

export interface FloatingPanelProps {
  anchor: AnchorRect | null
  placement?: Placement
  children: ReactNode
  className?: string
  ref?: Ref<HTMLDivElement>
  role?: string
  'aria-label'?: string
  'aria-labelledby'?: string
  'aria-modal'?: boolean
  /** -1 = o painel recebe o foco num clique fora dos campos (os atalhos de teclado continuam nele). */
  tabIndex?: number
  onKeyDown?: KeyboardEventHandler<HTMLDivElement>
}

/** Painel flutuante (portal) ancorado a um retângulo da tela; sem âncora, fica centralizado. */
export function FloatingPanel({ anchor, placement = 'right', children, className, ref, ...rest }: FloatingPanelProps) {
  const innerRef = useRef<HTMLDivElement>(null)
  useImperativeHandle(ref, () => innerRef.current!, [])
  // opacity (e não visibility) para que o foco automático funcione antes do posicionamento
  const [style, setStyle] = useState<CSSProperties>({ opacity: 0, left: 0, top: 0 })

  useLayoutEffect(() => {
    const el = innerRef.current
    if (!el) return
    const update = () => {
      const panel = { width: el.offsetWidth, height: el.offsetHeight }
      const viewport = { width: window.innerWidth, height: window.innerHeight }
      const box = anchor
        ? placePanel(anchor, panel, viewport, placement)
        : { left: (viewport.width - panel.width) / 2, top: Math.max(MARGIN, (viewport.height - panel.height) / 3) }
      setStyle((s) =>
        s.left === box.left && s.top === box.top && s.opacity === 1 ? s : { left: box.left, top: box.top, opacity: 1 },
      )
    }
    update()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    observer?.observe(el)
    window.addEventListener('resize', update)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', update)
    }
  }, [anchor, placement])

  return createPortal(
    <div
      ref={innerRef}
      style={style}
      className={cn(
        'fixed z-50 animate-pop-in rounded-lg border border-line-strong bg-surface shadow-float',
        className,
      )}
      {...rest}
    >
      {children}
    </div>,
    document.body,
  )
}
