import { create } from 'zustand'

import { type ApiError, isApiError } from '../api/errors'
import { api } from '../api/endpoints'
import type { UserOut } from '../api/types'

const STORAGE_KEY = 'agenda-salas:auth'

export type AuthStatus = 'anonymous' | 'restoring' | 'authenticated'
/**
 * Por que a sessão terminou: `manual` (Sair, ou saiu em outra aba), `expired` (token vencido ou
 * inválido), `revoked` (a senha mudou: SESSION_REVOKED) ou `disabled` (conta desativada).
 */
export type LogoutReason = 'manual' | 'expired' | 'revoked' | 'disabled'

/** Aviso da tela de login para cada motivo de fim de sessão (contrato: D2). */
export const LOGOUT_MESSAGES: Record<Exclude<LogoutReason, 'manual'>, string> = {
  expired: 'Sua sessão expirou. Entre novamente.',
  revoked: 'Sua sessão foi encerrada porque a senha foi alterada. Entre novamente.',
  disabled: 'Seu acesso foi desativado. Fale com o administrador.',
}

/** Motivo do fim de sessão a partir do erro da API (detail.code do 401). */
export function logoutReasonFor(error: Pick<ApiError, 'code'> | unknown): LogoutReason {
  const code = isApiError(error) ? error.code : null
  if (code === 'SESSION_REVOKED') return 'revoked'
  if (code === 'ACCOUNT_DISABLED') return 'disabled'
  return 'expired'
}

interface StoredSession {
  token: string
  expiresAt: string
  user: UserOut
}

interface LogoutOptions {
  /** A sessão já saiu do navegador (outra aba saiu ou trocou de usuário): não mexe no armazenamento. */
  keepStorage?: boolean
}

interface AuthState {
  token: string | null
  expiresAt: string | null
  user: UserOut | null
  status: AuthStatus
  /** Por que a última sessão terminou (a tela de login mostra o aviso correspondente). */
  logoutReason: LogoutReason | null
  /** Usuário (login) da sessão que terminou: o próximo login só volta à página anterior se for o mesmo. */
  previousUsername: string | null
  login: (username: string, password: string) => Promise<void>
  /** Confirma no servidor a sessão salva no navegador. */
  restore: () => Promise<void>
  logout: (reason?: LogoutReason, options?: LogoutOptions) => void
}

function readStored(): StoredSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<StoredSession>
    if (!parsed.token || !parsed.expiresAt || !parsed.user) return null
    if (Date.parse(parsed.expiresAt) <= Date.now()) return null
    return parsed as StoredSession
  } catch {
    return null
  }
}

function writeStored(session: StoredSession | null): void {
  try {
    if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // armazenamento indisponível (janela privada): a sessão vale só para esta aba
  }
}

let expiryTimer: ReturnType<typeof setTimeout> | undefined
const logoutListeners = new Set<() => void>()

/** Permite que outros módulos limpem seu estado ao sair (evita import circular). */
export function onLogout(listener: () => void): () => void {
  logoutListeners.add(listener)
  return () => logoutListeners.delete(listener)
}

function scheduleExpiry(expiresAt: string | null): void {
  clearTimeout(expiryTimer)
  if (!expiresAt) return
  const ms = Date.parse(expiresAt) - Date.now()
  // setTimeout aceita no máximo ~24,8 dias; tokens duram um turno (12 h)
  if (ms > 0 && ms < 2_000_000_000) {
    expiryTimer = setTimeout(() => useAuth.getState().logout('expired'), ms)
  }
}

const stored = readStored()

export const useAuth = create<AuthState>()((set, get) => ({
  token: stored?.token ?? null,
  expiresAt: stored?.expiresAt ?? null,
  user: stored?.user ?? null,
  status: stored ? 'restoring' : 'anonymous',
  logoutReason: null,
  previousUsername: null,

  login: async (username, password) => {
    const result = await api.auth.login({ username: username.trim(), password })
    const session = { token: result.access_token, expiresAt: result.expires_at, user: result.user }
    writeStored(session)
    scheduleExpiry(session.expiresAt)
    set({ ...session, status: 'authenticated', logoutReason: null })
  },

  restore: async () => {
    const { token, expiresAt } = get()
    if (!token) {
      set({ status: 'anonymous' })
      return
    }
    try {
      const user = await api.auth.me()
      if (get().token !== token) return // outra aba trocou a sessão no meio
      writeStored({ token, expiresAt: expiresAt ?? '', user })
      scheduleExpiry(expiresAt)
      set({ user, status: 'authenticated' })
    } catch (error) {
      if (isApiError(error) && (error.status === 401 || error.status === 403)) {
        get().logout(logoutReasonFor(error))
      } else {
        // Servidor fora do ar: segue com a sessão salva; as chamadas seguintes tentam de novo.
        scheduleExpiry(expiresAt)
        set({ status: 'authenticated' })
      }
    }
  },

  logout: (reason = 'manual', { keepStorage = false } = {}) => {
    if (get().status === 'anonymous' && !get().token) return
    clearTimeout(expiryTimer)
    if (!keepStorage) writeStored(null)
    set({
      token: null,
      expiresAt: null,
      user: null,
      status: 'anonymous',
      logoutReason: reason,
      previousUsername: get().user?.username ?? get().previousUsername,
    })
    for (const listener of logoutListeners) listener()
  },
}))

export function useIsAdmin(): boolean {
  return useAuth((s) => s.user?.role === 'ADMIN')
}

/** Mesmo login (sem diferenciar maiúsculas): decide se o login seguinte volta à página anterior. */
export function sameUsername(a: string | null | undefined, b: string | null | undefined): boolean {
  return Boolean(a && b && a.trim().toLowerCase() === b.trim().toLowerCase())
}

let reloadPage: () => void = () => window.location.reload()

/**
 * A sessão é uma só para todas as abas (localStorage). Quando outra aba sai, entra ou troca de
 * usuário, esta acompanha — nunca continua agindo (tempo real, travas, gravações) como a pessoa
 * anterior (R14).
 */
export function syncSessionFromStorage(): void {
  const next = readStored()
  const state = useAuth.getState()
  if (!next) {
    // Saiu em outra aba (ou a sessão salva venceu): sai aqui também, sem apagar nada.
    if (state.token) state.logout('manual', { keepStorage: true })
    return
  }
  if (next.token === state.token) return
  if (!state.token) {
    // Esta aba estava no login: entra junto.
    scheduleExpiry(next.expiresAt)
    useAuth.setState({
      token: next.token,
      expiresAt: next.expiresAt,
      user: next.user,
      status: 'authenticated',
      logoutReason: null,
    })
    return
  }
  if (sameUsername(state.user?.username, next.user.username)) {
    // A mesma pessoa entrou de novo (token novo): adota sem interromper o trabalho.
    scheduleExpiry(next.expiresAt)
    useAuth.setState({ token: next.token, expiresAt: next.expiresAt, user: next.user })
    return
  }
  // Outra pessoa entrou neste navegador: encerra esta sessão e recarrega já como ela.
  state.logout('manual', { keepStorage: true })
  reloadPage()
}

let syncInstalled = false

/** Liga a sincronização entre abas (evento `storage`); `reload` só é trocado nos testes. */
export function installSessionSync(options: { reload?: () => void } = {}): void {
  if (options.reload) reloadPage = options.reload
  if (syncInstalled || typeof window === 'undefined') return
  syncInstalled = true
  window.addEventListener('storage', (event) => {
    if (event.key !== null && event.key !== STORAGE_KEY) return
    syncSessionFromStorage()
  })
}
