import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import { ADMIN } from '../test/fixtures'
import { installSessionSync, LOGOUT_MESSAGES, logoutReasonFor, onLogout, useAuth } from './auth'

vi.mock('../api/endpoints', () => ({
  api: { auth: { login: vi.fn(), me: vi.fn() } },
}))

const { api } = await import('../api/endpoints')

const KEY = 'agenda-salas:auth'

beforeEach(() => {
  vi.clearAllMocks()
  useAuth.setState({
    token: null,
    user: null,
    expiresAt: null,
    status: 'anonymous',
    logoutReason: null,
    previousUsername: null,
  })
})

describe('auth store', () => {
  it('login guarda a sessão no navegador', async () => {
    const expires = new Date(Date.now() + 3_600_000).toISOString()
    vi.mocked(api.auth.login).mockResolvedValue({
      access_token: 'jwt',
      token_type: 'bearer',
      expires_at: expires,
      user: ADMIN,
    })
    await useAuth.getState().login('  ana ', 'segredo')
    expect(api.auth.login).toHaveBeenCalledWith({ username: 'ana', password: 'segredo' })
    expect(useAuth.getState()).toMatchObject({ token: 'jwt', status: 'authenticated', user: ADMIN })
    expect(JSON.parse(localStorage.getItem(KEY)!)).toMatchObject({ token: 'jwt', expiresAt: expires })
  })

  it('falha de login propaga o erro sem mudar o estado', async () => {
    vi.mocked(api.auth.login).mockRejectedValue(
      new ApiError(401, 'INVALID_CREDENTIALS', 'Usuário ou senha incorretos.'),
    )
    await expect(useAuth.getState().login('ana', 'x')).rejects.toMatchObject({ code: 'INVALID_CREDENTIALS' })
    expect(useAuth.getState().status).toBe('anonymous')
  })

  it('restore confirma a sessão salva; 401 → sessão expirada', async () => {
    useAuth.setState({ token: 'jwt', expiresAt: new Date(Date.now() + 60_000).toISOString(), status: 'restoring' })
    vi.mocked(api.auth.me).mockResolvedValueOnce(ADMIN)
    await useAuth.getState().restore()
    expect(useAuth.getState().status).toBe('authenticated')

    useAuth.setState({ status: 'restoring' })
    vi.mocked(api.auth.me).mockRejectedValueOnce(new ApiError(401, 'UNAUTHORIZED', 'Sessão expirada.'))
    await useAuth.getState().restore()
    expect(useAuth.getState()).toMatchObject({ status: 'anonymous', token: null, logoutReason: 'expired' })
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('restore com servidor fora do ar mantém a sessão', async () => {
    useAuth.setState({
      token: 'jwt',
      user: ADMIN,
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
      status: 'restoring',
    })
    vi.mocked(api.auth.me).mockRejectedValueOnce(new ApiError(0, 'NETWORK_ERROR', 'Sem conexão.'))
    await useAuth.getState().restore()
    expect(useAuth.getState()).toMatchObject({ status: 'authenticated', token: 'jwt' })
  })

  it('logout limpa tudo e avisa os ouvintes (uma vez)', () => {
    const listener = vi.fn()
    const off = onLogout(listener)
    useAuth.setState({ token: 'jwt', user: ADMIN, status: 'authenticated' })
    localStorage.setItem(KEY, '{}')
    useAuth.getState().logout('manual')
    useAuth.getState().logout('expired')
    expect(listener).toHaveBeenCalledOnce()
    expect(useAuth.getState()).toMatchObject({ token: null, user: null, logoutReason: 'manual' })
    expect(localStorage.getItem(KEY)).toBeNull()
    off()
  })

  it('sessão vence sozinha no expires_at', async () => {
    vi.useFakeTimers()
    try {
      const expires = new Date(Date.now() + 5_000).toISOString()
      vi.mocked(api.auth.login).mockResolvedValue({
        access_token: 'jwt',
        token_type: 'bearer',
        expires_at: expires,
        user: ADMIN,
      })
      await useAuth.getState().login('ana', 'segredo')
      vi.advanceTimersByTime(5_001)
      expect(useAuth.getState()).toMatchObject({ status: 'anonymous', logoutReason: 'expired' })
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('motivo do fim da sessão (D2)', () => {
  it('logoutReasonFor: SESSION_REVOKED → revoked, ACCOUNT_DISABLED → disabled, resto → expired', () => {
    expect(logoutReasonFor(new ApiError(401, 'SESSION_REVOKED', 'x'))).toBe('revoked')
    expect(logoutReasonFor(new ApiError(401, 'ACCOUNT_DISABLED', 'x'))).toBe('disabled')
    expect(logoutReasonFor(new ApiError(401, 'UNAUTHORIZED', 'x'))).toBe('expired')
    expect(logoutReasonFor(new Error('x'))).toBe('expired')
  })

  it('restore com 401 SESSION_REVOKED sai com o motivo "senha alterada"', async () => {
    useAuth.setState({ token: 'jwt', expiresAt: new Date(Date.now() + 60_000).toISOString(), status: 'restoring' })
    vi.mocked(api.auth.me).mockRejectedValueOnce(new ApiError(401, 'SESSION_REVOKED', 'A senha foi alterada.'))
    await useAuth.getState().restore()
    expect(useAuth.getState()).toMatchObject({ status: 'anonymous', logoutReason: 'revoked' })
    expect(LOGOUT_MESSAGES.revoked).toBe('Sua sessão foi encerrada porque a senha foi alterada. Entre novamente.')
  })

  it('logout guarda quem estava logado (o próximo login decide se volta à página anterior)', () => {
    useAuth.setState({ token: 'jwt', user: ADMIN, status: 'authenticated' })
    useAuth.getState().logout('expired')
    expect(useAuth.getState().previousUsername).toBe(ADMIN.username)
  })
})

describe('sessão compartilhada entre abas (R14)', () => {
  const OTHER = { ...ADMIN, id: 7, username: 'beatriz', full_name: 'Beatriz Lima', role: 'RECEPTIONIST' as const }
  const future = () => new Date(Date.now() + 3_600_000).toISOString()
  const store = (token: string, user = ADMIN) =>
    localStorage.setItem(KEY, JSON.stringify({ token, expiresAt: future(), user }))
  const reload = vi.fn()

  beforeEach(() => {
    reload.mockReset()
    installSessionSync({ reload })
  })

  it('outra aba saiu: esta sai também, sem mexer no armazenamento', () => {
    const listener = vi.fn()
    const off = onLogout(listener)
    useAuth.setState({ token: 'jwt', user: ADMIN, status: 'authenticated' })
    localStorage.removeItem(KEY)
    window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: null }))
    expect(useAuth.getState()).toMatchObject({ status: 'anonymous', token: null, logoutReason: 'manual' })
    expect(listener).toHaveBeenCalledOnce()
    off()
  })

  it('outra aba entrou (esta estava no login): entra junto', () => {
    store('novo')
    window.dispatchEvent(new StorageEvent('storage', { key: KEY }))
    expect(useAuth.getState()).toMatchObject({ status: 'authenticated', token: 'novo', user: ADMIN })
    expect(reload).not.toHaveBeenCalled()
  })

  it('a mesma pessoa entrou de novo em outra aba: adota o token novo sem recarregar', () => {
    useAuth.setState({ token: 'antigo', user: ADMIN, status: 'authenticated' })
    store('novo')
    window.dispatchEvent(new StorageEvent('storage', { key: KEY }))
    expect(useAuth.getState()).toMatchObject({ status: 'authenticated', token: 'novo' })
    expect(reload).not.toHaveBeenCalled()
  })

  it('outra pessoa entrou neste navegador: esta aba deixa de agir como a anterior e recarrega como a nova', () => {
    const listener = vi.fn()
    const off = onLogout(listener)
    useAuth.setState({ token: 'da-ana', user: ADMIN, status: 'authenticated' })
    store('da-beatriz', OTHER)
    window.dispatchEvent(new StorageEvent('storage', { key: KEY }))
    expect(useAuth.getState().token).toBeNull()
    expect(listener).toHaveBeenCalledOnce()
    expect(reload).toHaveBeenCalledOnce()
    // a sessão nova (da outra aba) continua salva
    expect(JSON.parse(localStorage.getItem(KEY)!)).toMatchObject({ token: 'da-beatriz' })
    off()
  })

  it('eventos de outras chaves são ignorados', () => {
    useAuth.setState({ token: 'jwt', user: ADMIN, status: 'authenticated' })
    window.dispatchEvent(new StorageEvent('storage', { key: 'outra-coisa' }))
    expect(useAuth.getState().token).toBe('jwt')
  })
})
