import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RoomOut } from '../api/types'
import { makeRoom } from '../test/fixtures'
import { useRooms } from './rooms'

vi.mock('../api/endpoints', () => ({ api: { rooms: { list: vi.fn() } } }))

const { api } = await import('../api/endpoints')

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.clearAllMocks()
  useRooms.getState().reset()
})

describe('useRooms', () => {
  it('R25: a resposta de uma busca não desfaz uma sala recebida por evento depois que ela começou', async () => {
    const reply = deferred<RoomOut[]>()
    vi.mocked(api.rooms.list).mockReturnValueOnce(reply.promise)
    const loading = useRooms.getState().load()
    useRooms.getState().upsert(makeRoom({ id: 1, name: 'Sala 01 (renomeada)', capacity: 20 }))
    reply.resolve([makeRoom({ id: 1, name: 'Sala 01', capacity: 8 }), makeRoom({ id: 2, name: 'Sala 02' })])
    await loading
    expect(useRooms.getState().byId[1]).toMatchObject({ name: 'Sala 01 (renomeada)', capacity: 20 })
    expect(useRooms.getState().byId[2]?.name).toBe('Sala 02')
    expect(useRooms.getState().status).toBe('ready')
  })

  it('R25: uma busca mais velha que termina depois não sobrescreve a mais nova', async () => {
    const old = deferred<RoomOut[]>()
    const fresh = deferred<RoomOut[]>()
    vi.mocked(api.rooms.list).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
    const first = useRooms.getState().load()
    const second = useRooms.getState().load()
    fresh.resolve([makeRoom({ id: 1, name: 'Nova' })])
    await second
    old.resolve([makeRoom({ id: 1, name: 'Velha' })])
    await first
    expect(useRooms.getState().byId[1]?.name).toBe('Nova')
  })

  it('R15: lista inicial falhou → status error; uma sala avulsa por evento dispara uma nova busca', async () => {
    vi.mocked(api.rooms.list).mockRejectedValueOnce(new Error('500'))
    await useRooms.getState().load()
    expect(useRooms.getState().status).toBe('error')
    expect(useRooms.getState().error).toBe('Não foi possível carregar as salas.')

    vi.mocked(api.rooms.list).mockResolvedValueOnce([makeRoom({ id: 1 }), makeRoom({ id: 2, name: 'Sala 02' })])
    useRooms.getState().upsert(makeRoom({ id: 1 }))
    await vi.waitFor(() => expect(useRooms.getState().status).toBe('ready'))
    expect(Object.keys(useRooms.getState().byId)).toHaveLength(2)
  })
})
