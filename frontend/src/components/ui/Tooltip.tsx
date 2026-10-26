import { type ReactNode, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { cn } from '../../lib/cn'

interface Position {
  left: number
  top: number
  below: boolean
}

/** Dica ao passar o mouse ou focar (com atraso), posicionada acima do alvo e mantida na tela. */
export function Tooltip({
  content,
  children,
  delayMs = 350,
  className,
}: {
  content: ReactNode
  children: ReactNode
  delayMs?: number
  className?: string
}) {
  const id = useId()
  const triggerRef = useRef<HTMLSpanElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [position, setPosition] = useState<Position | null>(null)

  const show = () => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return
      const below = rect.top < 48
      setPosition({ left: rect.left + rect.width / 2, top: below ? rect.bottom + 6 : rect.top - 6, below })
    }, delayMs)
  }
  const hide = () => {
    clearTimeout(timer.current)
    setPosition(null)
  }

  useLayoutEffect(() => {
    const tip = tipRef.current
    if (!tip || !position) return
    const half = tip.offsetWidth / 2
    tip.style.left = `${Math.min(Math.max(position.left, 8 + half), window.innerWidth - 8 - half)}px`
  }, [position])

  useLayoutEffect(() => () => clearTimeout(timer.current), [])

  if (!content) return <>{children}</>

  return (
    <>
      <span
        ref={triggerRef}
        className={cn('inline-flex min-w-0', className)}
        onPointerEnter={show}
        onPointerLeave={hide}
        onPointerDown={hide}
        onFocus={show}
        onBlur={hide}
        aria-describedby={position ? id : undefined}
      >
        {children}
      </span>
      {position &&
        createPortal(
          <div
            ref={tipRef}
            id={id}
            role="tooltip"
            style={{ left: position.left, top: position.top }}
            className={cn(
              'pointer-events-none fixed z-[200] max-w-[300px] -translate-x-1/2 animate-fade-in rounded-md bg-ink px-2.5 py-1.5 text-xs leading-snug text-white shadow-float',
              !position.below && '-translate-y-full',
            )}
          >
            {content}
          </div>,
          document.body,
        )}
    </>
  )
}
