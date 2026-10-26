import { type ReactNode, type RefObject, useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

import { focusableElements, useFocusTrap } from '../../hooks/useFocusTrap'
import { cn } from '../../lib/cn'

export interface DialogProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  size?: 'sm' | 'md' | 'lg'
  initialFocusRef?: RefObject<HTMLElement | null>
  /** Impede fechar (ex.: enquanto salva). */
  busy?: boolean
}

const SIZES = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl' }

export function Dialog(props: DialogProps) {
  if (!props.open) return null
  return createPortal(<DialogPanel {...props} />, document.body)
}

function DialogPanel({
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
  initialFocusRef,
  busy,
}: DialogProps) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const descriptionId = useId()
  useFocusTrap(ref)

  useEffect(() => {
    // Foco inicial: o alvo pedido ou o primeiro campo depois do botão de fechar.
    const items = ref.current ? focusableElements(ref.current) : []
    const target = initialFocusRef?.current ?? items[1] ?? items[0] ?? ref.current
    target?.focus()
  }, [initialFocusRef])

  // O botão clicado fica desabilitado enquanto salva e o navegador manda o foco para o <body>:
  // o Esc do painel (onKeyDown abaixo) não dispara mais, então o diálogo também escuta no documento.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || busy) return
      const active = document.activeElement
      if (active && active !== document.body) return
      onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [busy, onClose])

  return (
    <div
      className="fixed inset-0 z-[60] flex animate-fade-in items-start justify-center overflow-y-auto bg-ink/35 px-4 py-[8vh]"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose()
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation()
            if (!busy) onClose()
          }
        }}
        className={cn(
          'w-full animate-pop-in rounded-xl border border-line-strong bg-surface shadow-float outline-none',
          SIZES[size],
        )}
      >
        <header className="flex items-start gap-3 border-b border-line px-5 pt-4 pb-3">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-[15px] font-semibold text-ink">
              {title}
            </h2>
            {description && (
              <p id={descriptionId} className="mt-0.5 text-[13px] text-muted">
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Fechar"
            className="-mr-1.5 rounded-md p-1 text-muted hover:bg-ink/[0.06] hover:text-ink"
          >
            <X className="size-4" />
          </button>
        </header>
        {children && <div className="px-5 py-4">{children}</div>}
        {footer && (
          <footer className="flex items-center justify-end gap-2 rounded-b-xl border-t border-line bg-paper/70 px-5 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>
  )
}
