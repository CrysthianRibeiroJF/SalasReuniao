import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'

import { cn } from '../../lib/cn'
import { type Toast, type ToastKind, useToasts } from '../../store/toasts'

const ICONS: Record<ToastKind, typeof Info> = {
  success: CheckCircle2,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
}

const ACCENT: Record<ToastKind, string> = {
  success: 'text-success',
  error: 'text-danger',
  warning: 'text-warning',
  info: 'text-video',
}

function ToastItem({ toast }: { toast: Toast }) {
  const dismiss = useToasts((s) => s.dismiss)
  const [paused, setPaused] = useState(false)
  const remaining = useRef(toast.durationMs)
  const startedAt = useRef(0)

  useEffect(() => {
    if (paused) return
    startedAt.current = Date.now()
    const timer = setTimeout(() => dismiss(toast.id), remaining.current)
    return () => {
      clearTimeout(timer)
      remaining.current -= Date.now() - startedAt.current
    }
  }, [paused, dismiss, toast.id])

  const Icon = ICONS[toast.kind]
  return (
    <div
      role={toast.kind === 'error' ? 'alert' : 'status'}
      onPointerEnter={() => setPaused(true)}
      onPointerLeave={() => setPaused(false)}
      className="pointer-events-auto flex w-[360px] max-w-[calc(100vw-24px)] animate-toast-in items-start gap-2.5 rounded-lg border border-line-strong bg-surface px-3 py-2.5 shadow-float"
    >
      <Icon className={cn('mt-px size-4 shrink-0', ACCENT[toast.kind])} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] leading-snug font-medium text-ink">{toast.title}</p>
        {toast.description && <p className="mt-0.5 text-[12.5px] leading-snug text-muted">{toast.description}</p>}
        {toast.action && (
          <button
            type="button"
            onClick={() => {
              toast.action?.onClick()
              dismiss(toast.id)
            }}
            className="mt-1.5 text-[12.5px] font-semibold text-video hover:underline"
          >
            {toast.action.label}
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={() => dismiss(toast.id)}
        aria-label="Fechar aviso"
        className="-mt-0.5 -mr-1 rounded p-0.5 text-faint hover:bg-ink/[0.06] hover:text-ink"
      >
        <X className="size-3.5" />
      </button>
    </div>
  )
}

export type ToasterPlacement = 'bottom-right' | 'bottom-left'

const PLACEMENTS: Record<ToasterPlacement, string> = {
  // Grade: acima das abas diárias, à direita.
  'bottom-right': 'right-3 bottom-14 items-end',
  // Administração: à esquerda, longe dos botões das tabelas (Editar, Desativar…), que ficam à direita.
  'bottom-left': 'left-3 bottom-3 items-start',
}

export function Toaster({ placement = 'bottom-right' }: { placement?: ToasterPlacement }) {
  const toasts = useToasts((s) => s.toasts)
  return (
    <div
      aria-live="polite"
      data-dismiss-ignore=""
      data-placement={placement}
      className={cn('pointer-events-none fixed z-[150] flex flex-col gap-2', PLACEMENTS[placement])}
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} />
      ))}
    </div>
  )
}
