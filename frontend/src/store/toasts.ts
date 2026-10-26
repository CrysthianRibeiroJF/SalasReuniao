import { create } from 'zustand'

export type ToastKind = 'success' | 'error' | 'warning' | 'info'

export interface ToastAction {
  label: string
  onClick: () => void
}

export interface Toast {
  id: number
  kind: ToastKind
  title: string
  description?: string
  action?: ToastAction
  durationMs: number
}

interface ToastState {
  toasts: Toast[]
  push: (toast: Omit<Toast, 'id' | 'durationMs'> & { durationMs?: number }) => number
  dismiss: (id: number) => void
  clear: () => void
}

const MAX_VISIBLE = 4
const DEFAULT_DURATION: Record<ToastKind, number> = { success: 4_000, info: 5_000, warning: 8_000, error: 8_000 }

let nextId = 1

export const useToasts = create<ToastState>()((set) => ({
  toasts: [],
  push: (toast) => {
    const id = nextId++
    const durationMs = toast.durationMs ?? DEFAULT_DURATION[toast.kind]
    set((s) => ({ toasts: [...s.toasts, { ...toast, id, durationMs }].slice(-MAX_VISIBLE) }))
    return id
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  clear: () => set({ toasts: [] }),
}))

type ToastOptions = Partial<Pick<Toast, 'description' | 'action' | 'durationMs'>>

/** Atalhos: toast.success('Reserva salva', { description }) */
export const toast = {
  success: (title: string, options?: ToastOptions) => useToasts.getState().push({ kind: 'success', title, ...options }),
  error: (title: string, options?: ToastOptions) => useToasts.getState().push({ kind: 'error', title, ...options }),
  warning: (title: string, options?: ToastOptions) => useToasts.getState().push({ kind: 'warning', title, ...options }),
  info: (title: string, options?: ToastOptions) => useToasts.getState().push({ kind: 'info', title, ...options }),
}
