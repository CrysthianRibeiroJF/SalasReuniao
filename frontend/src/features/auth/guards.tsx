import { type ReactNode, useEffect } from 'react'
import { Navigate, useLocation } from 'react-router'

import { useAuth, useIsAdmin } from '../../store/auth'
import { FullScreenMessage } from '../shell/FullScreenMessage'

let restoring: Promise<void> | null = null

export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuth((s) => s.status)
  const logoutReason = useAuth((s) => s.logoutReason)
  const location = useLocation()

  useEffect(() => {
    if (status === 'restoring' && !restoring) {
      restoring = useAuth
        .getState()
        .restore()
        .finally(() => {
          restoring = null
        })
    }
  }, [status])

  if (status === 'anonymous') {
    // Volta para onde estava só quando a sessão caiu sozinha (vencida/senha alterada) ou num link
    // direto; depois de "Sair" ou de uma conta desativada o próximo login começa na grade (R40).
    const keepPlace = logoutReason !== 'manual' && logoutReason !== 'disabled'
    return (
      <Navigate
        to="/login"
        replace
        state={keepPlace ? { from: `${location.pathname}${location.search}` } : undefined}
      />
    )
  }
  if (status === 'restoring') return <FullScreenMessage loading title="Abrindo a agenda…" />
  return <>{children}</>
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const isAdmin = useIsAdmin()
  if (!isAdmin) return <Navigate to="/admin/auditoria" replace />
  return <>{children}</>
}
