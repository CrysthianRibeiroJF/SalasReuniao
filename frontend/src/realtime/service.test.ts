import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import { useAuth } from '../store/auth'
import { useBookings } from '../store/bookings'
import { useRooms } from '../store/rooms'
import { useUi } from '../store/ui'
import { ADMIN } from '../test/fixtures'
import type { RealtimeClientOptions } from './socket'

const created: RealtimeClientOptions[] = []

vi.mock('./socket', () => ({
  buildSocketUrl: (token: string) => `ws://test/ws?token=${token}`,
  RealtimeClient: class {
    readonly options: RealtimeClientOptions
    constructor(options: RealtimeClientOptions) {
      this.options = options
      created.push(options)
    }
    start() {
      this.options.url()
    }
    stop() {}
  },
}))

vi.mock('../api/endpoints', () => ({
  api: {
    auth: { me: vi.fn() },
    rooms: { list: vi.fn(async () => []) },
    bookings: { listDay: vi.fn(async () => []), listRange: vi.fn(async () => []) },
  },
}))

const { api } = await import('../api/endpoints')
const { startRealtime, stopRealtime } = await import('./service')

beforeEach(() => {
  vi.clearAllMocks()
  created.length = 0
  useAuth.setState({ token: 'tok-1', user: ADMIN, status: 'authenticated', logoutReason: null })
  useUi.getState().reset()
  useBookings.getState().reset()
  useRooms.getState().reset()
})

afterEach(() => stopRealtime())

describe('realtime/service', () => {
  it('R25: no primeiro HELLO recarrega a semana mesmo com a busca inicial ainda em andamento', async () => {
    useUi.getState().setActiveDate('2026-11-24')
    const week = vi.mocked(api.bookings.listRange)
    week.mockReturnValueOnce(new Promise(() => {})) // a busca inicial não voltou
    void useBookings.getState().loadWeek('2026-11-24')
    expect(useBookings.getState().data.dates['2026-11-24']?.status).toBe('loading')

    startRealtime()
    created[0]!.onReady?.({ reconnect: false })
    expect(week).toHaveBeenCalledTimes(2)
    // as salas também: a busca delas começou antes de a conexão ser registrada no servidor
    expect(api.rooms.list).toHaveBeenCalledOnce()
  })

  it('4401 com o mesmo token: pergunta o motivo ao servidor e sai como "expirada" se nada mais disser', async () => {
    vi.mocked(api.auth.me).mockRejectedValueOnce(new ApiError(0, 'NETWORK_ERROR', 'Sem conexão.'))
    startRealtime()
    created[0]!.onUnauthorized?.()
    await vi.waitFor(() => expect(useAuth.getState().status).toBe('anonymous'))
    expect(api.auth.me).toHaveBeenCalledOnce()
    expect(useAuth.getState().logoutReason).toBe('expired')
  })

  it('4401 depois que outra aba trocou o token: só reconecta com o token novo', () => {
    startRealtime()
    useAuth.setState({ token: 'tok-2' })
    created[0]!.onUnauthorized?.()
    expect(created).toHaveLength(2)
    expect(useAuth.getState().status).toBe('authenticated')
    expect(api.auth.me).not.toHaveBeenCalled()
  })

  it('4403 = acesso desativado (D2)', () => {
    startRealtime()
    created[0]!.onForbidden?.()
    expect(useAuth.getState()).toMatchObject({ status: 'anonymous', logoutReason: 'disabled' })
  })
})
