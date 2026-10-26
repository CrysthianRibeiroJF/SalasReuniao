import { configureClient } from './api/client'
import { installSessionSync, logoutReasonFor, useAuth } from './store/auth'
import { installSessionReset } from './store/session'

/**
 * Liga o cliente HTTP à sessão: Bearer em toda chamada e 401 → volta para o login com o motivo
 * (sessão expirada, senha alterada ou acesso desativado). Também acompanha login/logout de outras abas.
 */
export function bootstrap(): void {
  configureClient({
    getToken: () => useAuth.getState().token,
    onUnauthorized: (error) => useAuth.getState().logout(logoutReasonFor(error)),
  })
  installSessionReset()
  installSessionSync()
}
