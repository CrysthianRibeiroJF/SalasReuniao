import { onLogout } from './auth'
import { useBookings } from './bookings'
import { useConfig } from './config'
import { useLocks } from './locks'
import { usePresence } from './presence'
import { useRealtime } from './realtime'
import { useRooms } from './rooms'
import { useToasts } from './toasts'
import { useUi } from './ui'

/** Limpa todo o estado da sessão (logout ou troca de usuário). */
export function resetSessionState(): void {
  useBookings.getState().reset()
  useLocks.getState().reset()
  usePresence.getState().reset()
  useRealtime.getState().reset()
  useRooms.getState().reset()
  useUi.getState().reset()
  useConfig.getState().reset()
  useToasts.getState().clear()
}

let installed = false

export function installSessionReset(): void {
  if (installed) return
  installed = true
  onLogout(resetSessionState)
}
