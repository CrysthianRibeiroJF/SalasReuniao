import { useEffect } from 'react'
import { Outlet, useLocation } from 'react-router'
import { RefreshCw } from 'lucide-react'

import { Button } from '../../components/ui/Button'
import { Skeleton } from '../../components/ui/Skeleton'
import { Toaster } from '../../components/ui/Toaster'
import { startRealtime, stopRealtime } from '../../realtime/service'
import { useConfig } from '../../store/config'
import { useRooms } from '../../store/rooms'
import { FullScreenMessage } from './FullScreenMessage'

const RETRY_MS = 5_000

/** Área logada: carrega configuração e salas, abre o tempo real e hospeda os avisos (toasts). */
export function AppShell() {
  const status = useConfig((s) => s.status)
  const error = useConfig((s) => s.error)
  const roomsStatus = useRooms((s) => s.status)
  const { pathname } = useLocation()

  useEffect(() => {
    void useConfig.getState().load()
    void useRooms.getState().load()
    startRealtime()
    return () => stopRealtime()
  }, [])

  // Servidor reiniciando/fora do ar: tenta de novo sozinho, sem exigir o clique.
  useEffect(() => {
    if (status !== 'error') return
    const timer = setTimeout(() => {
      void useConfig.getState().load()
      void useRooms.getState().load()
    }, RETRY_MS)
    return () => clearTimeout(timer)
  }, [status])

  // Só a lista de salas falhou (a configuração veio): a grade mostra o erro e tenta de novo sozinha (R15).
  useEffect(() => {
    if (roomsStatus !== 'error' || status === 'error') return
    const timer = setTimeout(() => void useRooms.getState().load(), RETRY_MS)
    return () => clearTimeout(timer)
  }, [roomsStatus, status])

  if (error && status !== 'ready') {
    return (
      <FullScreenMessage
        title="Não foi possível abrir a agenda"
        description={`${error ?? 'O servidor não respondeu.'} Tentando de novo automaticamente…`}
        action={
          <Button
            icon={<RefreshCw className="size-4" />}
            loading={status === 'loading'}
            onClick={() => void useConfig.getState().load()}
          >
            Tentar de novo
          </Button>
        }
      />
    )
  }

  return (
    <>
      {status === 'ready' ? <Outlet /> : <ShellSkeleton />}
      {/* Na administração as ações das tabelas ficam à direita: os avisos vão para a esquerda (K8). */}
      <Toaster placement={pathname.startsWith('/admin') ? 'bottom-left' : 'bottom-right'} />
    </>
  )
}

/** Esqueleto no formato da grade enquanto a configuração chega. */
function ShellSkeleton() {
  return (
    <div className="flex h-full flex-col" aria-busy="true" aria-label="Carregando a agenda">
      <div className="flex h-12 items-center gap-4 border-b border-line bg-surface px-4">
        <Skeleton className="h-6 w-36" />
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-7 w-80" />
      </div>
      <div className="flex min-h-0 flex-1 gap-px bg-line p-px">
        <div className="w-14 bg-surface" />
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="flex flex-1 flex-col gap-2 bg-surface p-2">
            <Skeleton className="h-8" />
            <Skeleton className="mt-6 h-16 opacity-60" />
            <Skeleton className="mt-10 h-10 opacity-40" />
          </div>
        ))}
      </div>
      <div className="h-10 border-t border-line bg-sheet" />
    </div>
  )
}
