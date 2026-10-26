import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ServerEvent } from '../api/types'
import { makeLock } from '../test/fixtures'
import { backoffDelay, buildSocketUrl, type ConnectionStatus, RealtimeClient, type SocketLike } from './socket'

class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = []
  readyState = 0
  sent: Record<string, unknown>[] = []
  closed: { code?: number } | null = null
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  readonly url: string

  constructor(url: string) {
    this.url = url
    FakeSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(JSON.parse(data) as Record<string, unknown>)
  }

  close(code?: number) {
    this.closed = { code }
    this.readyState = 3
  }

  // ------- lado do "servidor"
  open() {
    this.readyState = 1
    this.onopen?.(new Event('open'))
  }

  receive(event: Partial<ServerEvent> & { type: string; data?: unknown }) {
    this.onmessage?.(
      new MessageEvent('message', { data: JSON.stringify({ ts: new Date().toISOString(), data: {}, ...event }) }),
    )
  }

  hello() {
    this.receive({
      type: 'HELLO',
      data: {
        user: { id: 1, full_name: 'Ana' },
        instance_id: 'i1',
        server_time: new Date().toISOString(),
        locks: [],
        online: [],
      },
    })
  }

  drop(code = 1006) {
    this.readyState = 3
    this.onclose?.(new CloseEvent('close', { code }))
  }

  lastSent() {
    return this.sent[this.sent.length - 1]!
  }
}

function setup(overrides: Partial<ConstructorParameters<typeof RealtimeClient>[0]> = {}) {
  const events: ServerEvent[] = []
  const statuses: ConnectionStatus[] = []
  const ready: boolean[] = []
  const onUnauthorized = vi.fn()
  const client = new RealtimeClient({
    url: () => 'ws://test/ws?token=t',
    onEvent: (e) => events.push(e),
    onStatus: (s) => statuses.push(s),
    onReady: ({ reconnect }) => ready.push(reconnect),
    onUnauthorized,
    createSocket: (url) => new FakeSocket(url),
    random: () => 0,
    ...overrides,
  })
  return { client, events, statuses, ready, onUnauthorized }
}

const current = () => FakeSocket.instances[FakeSocket.instances.length - 1]!

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.instances = []
})

afterEach(() => {
  vi.useRealTimers()
})

describe('backoffDelay', () => {
  it('cresce exponencialmente até ~10 s, com jitter', () => {
    const noJitter = (a: number) => backoffDelay(a, { random: () => 0 })
    expect([0, 1, 2, 3, 4, 5, 9].map(noJitter)).toEqual([500, 1000, 2000, 4000, 8000, 10000, 10000])
    expect(backoffDelay(5, { random: () => 1 })).toBe(11000)
    expect(backoffDelay(0, { random: () => 1 })).toBe(650)
  })
})

describe('buildSocketUrl', () => {
  it('usa a mesma origem e ws/wss conforme o protocolo', () => {
    expect(buildSocketUrl('a b', { protocol: 'http:', host: 'localhost:5173' })).toBe(
      'ws://localhost:5173/ws?token=a%20b',
    )
    expect(buildSocketUrl('x', { protocol: 'https:', host: 'agenda.empresa.com' })).toBe(
      'wss://agenda.empresa.com/ws?token=x',
    )
  })
})

describe('RealtimeClient', () => {
  it('só fica "open" depois do HELLO e repassa eventos', () => {
    const { client, statuses, events, ready } = setup()
    client.start()
    expect(statuses).toEqual(['connecting'])
    current().open()
    expect(client.isOpen).toBe(false)
    current().hello()
    expect(statuses.at(-1)).toBe('open')
    expect(ready).toEqual([false])
    current().receive({ type: 'PRESENCE', data: { online: [] } })
    expect(events.map((e) => e.type)).toEqual(['HELLO', 'PRESENCE'])
  })

  it('reconecta com backoff e avisa que é reconexão (para recarregar o dia)', () => {
    const { client, statuses, ready } = setup()
    client.start()
    current().open()
    current().hello()
    current().drop()
    expect(statuses.at(-1)).toBe('reconnecting')
    expect(FakeSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(499)
    expect(FakeSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeSocket.instances).toHaveLength(2)
    // falha de novo antes do HELLO: espera dobra
    current().drop()
    vi.advanceTimersByTime(999)
    expect(FakeSocket.instances).toHaveLength(2)
    vi.advanceTimersByTime(1)
    expect(FakeSocket.instances).toHaveLength(3)
    current().open()
    current().hello()
    expect(ready).toEqual([false, true])
    // após conectar, o backoff recomeça
    current().drop()
    vi.advanceTimersByTime(500)
    expect(FakeSocket.instances).toHaveLength(4)
  })

  it('rede caiu (offline): fecha na hora; voltou (online): reconecta sem esperar o backoff', async () => {
    let notify: (online: boolean) => void = () => {}
    const unsubscribe = vi.fn()
    const { client, statuses, ready } = setup({
      connectivity: {
        subscribe: (listener) => {
          notify = listener
          return unsubscribe
        },
      },
    })
    client.start()
    current().open()
    current().hello()
    const pending = client.lockSlot({ room_id: 1, date: '2026-10-26', start: '08:00', end: '08:30' })

    notify(false)
    expect(current().closed?.code).toBe(4002)
    expect(statuses.at(-1)).toBe('reconnecting')
    expect(client.isOpen).toBe(false)
    await expect(pending).resolves.toMatchObject({ ok: false, reason: 'OFFLINE' })
    // Sem rede continua tentando com backoff (as tentativas falham).
    vi.advanceTimersByTime(500)
    expect(FakeSocket.instances).toHaveLength(2)
    current().drop()
    expect(statuses.at(-1)).toBe('reconnecting')

    // A rede voltou: tenta na hora, e o timer do backoff anterior não abre outra conexão.
    notify(true)
    expect(FakeSocket.instances).toHaveLength(3)
    current().open()
    current().hello()
    expect(statuses.at(-1)).toBe('open')
    expect(ready).toEqual([false, true])
    vi.advanceTimersByTime(30_000)
    expect(FakeSocket.instances).toHaveLength(3)
    // "online" com a conexão já aberta não faz nada.
    notify(true)
    expect(FakeSocket.instances).toHaveLength(3)

    client.stop()
    expect(unsubscribe).toHaveBeenCalledOnce()
    notify(false)
    expect(statuses.at(-1)).toBe('closed')
  })

  it('fechamento 4401 = sessão inválida: não reconecta e pede logout', () => {
    const { client, onUnauthorized, statuses } = setup()
    client.start()
    current().open()
    current().drop(4401)
    expect(onUnauthorized).toHaveBeenCalledOnce()
    expect(statuses.at(-1)).toBe('closed')
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('fechamento 4403 = usuário desativado: não reconecta e avisa (D2)', () => {
    const onForbidden = vi.fn()
    const { client, onUnauthorized, statuses } = setup({ onForbidden })
    client.start()
    current().open()
    current().hello()
    current().drop(4403)
    expect(onForbidden).toHaveBeenCalledOnce()
    expect(onUnauthorized).not.toHaveBeenCalled()
    expect(statuses.at(-1)).toBe('closed')
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('outros fechamentos (ex.: 1011 do servidor) continuam reconectando', () => {
    const { client } = setup()
    client.start()
    current().open()
    current().hello()
    current().drop(1011)
    vi.advanceTimersByTime(1_000)
    expect(FakeSocket.instances).toHaveLength(2)
  })

  it('stop() fecha e não reconecta', () => {
    const { client } = setup()
    client.start()
    current().open()
    current().hello()
    client.stop()
    expect(current().closed?.code).toBe(1000)
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('envia PING a cada 25 s e reconecta se o PONG não vier', () => {
    const { client } = setup()
    client.start()
    current().open()
    current().hello()
    vi.advanceTimersByTime(25_000)
    expect(current().lastSent()).toMatchObject({ type: 'PING', data: {} })
    current().receive({ type: 'PONG' })
    vi.advanceTimersByTime(10_000)
    expect(current().closed).toBeNull()
    vi.advanceTimersByTime(15_000) // segundo PING
    vi.advanceTimersByTime(10_000) // sem PONG
    expect(current().closed?.code).toBe(4000)
    vi.advanceTimersByTime(500)
    expect(FakeSocket.instances).toHaveLength(2)
  })

  it('correlaciona LOCK_SLOT com a resposta pelo request_id', async () => {
    const { client, events } = setup()
    client.start()
    current().open()
    current().hello()
    const pending = client.lockSlot({ room_id: 3, date: '2026-10-26', start: '14:00', end: '15:00' })
    const sent = current().lastSent()
    expect(sent).toMatchObject({ type: 'LOCK_SLOT', data: { room_id: 3, start: '14:00' } })
    expect(typeof sent.request_id).toBe('string')
    const lock = makeLock({ lock_id: 'L1', room_id: 3 })
    // resposta de outro pedido não resolve este
    current().receive({ type: 'LOCK_ACQUIRED', data: { lock: makeLock({ lock_id: 'other' }) }, request_id: 'outro' })
    current().receive({ type: 'LOCK_ACQUIRED', data: { lock }, request_id: sent.request_id as string })
    await expect(pending).resolves.toEqual({ ok: true, lock })
    expect(events.filter((e) => e.type === 'LOCK_ACQUIRED')).toHaveLength(2)
  })

  it('LOCK_DENIED devolve motivo, mensagem e quem segura', async () => {
    const { client } = setup()
    client.start()
    current().open()
    current().hello()
    const pending = client.lockSlot({ room_id: 3, date: '2026-10-26', start: '14:00', end: '15:00' })
    const conflict = makeLock({ user: { id: 2, full_name: 'Fulana' } })
    current().receive({
      type: 'LOCK_DENIED',
      data: { reason: 'LOCKED_BY_OTHER', message: 'Fulana está reservando este horário agora.', conflicts: [conflict] },
      request_id: current().lastSent().request_id as string,
    })
    await expect(pending).resolves.toEqual({
      ok: false,
      reason: 'DENIED',
      code: 'LOCKED_BY_OTHER',
      message: 'Fulana está reservando este horário agora.',
      conflicts: [conflict],
    })
  })

  it('pedido sem resposta expira (TIMEOUT); sem conexão responde OFFLINE na hora', async () => {
    const { client } = setup({ requestTimeoutMs: 6_000 })
    await expect(
      client.lockSlot({ room_id: 1, date: '2026-10-26', start: '08:00', end: '08:30' }),
    ).resolves.toMatchObject({
      ok: false,
      reason: 'OFFLINE',
    })
    client.start()
    current().open()
    current().hello()
    const pending = client.lockSlot({ room_id: 1, date: '2026-10-26', start: '08:00', end: '08:30' })
    vi.advanceTimersByTime(6_000)
    await expect(pending).resolves.toMatchObject({ ok: false, reason: 'TIMEOUT' })
  })

  it('queda da conexão resolve pedidos pendentes como OFFLINE', async () => {
    const { client } = setup()
    client.start()
    current().open()
    current().hello()
    const pending = client.lockSlot({ room_id: 1, date: '2026-10-26', start: '08:00', end: '08:30' })
    current().drop()
    await expect(pending).resolves.toMatchObject({ ok: false, reason: 'OFFLINE' })
  })

  it('renova e libera travas', async () => {
    const { client } = setup()
    client.start()
    current().open()
    current().hello()
    const refresh = client.refreshLock('L1')
    expect(current().lastSent()).toMatchObject({ type: 'REFRESH_LOCK', data: { lock_id: 'L1' } })
    const lock = makeLock({ lock_id: 'L1' })
    current().receive({ type: 'LOCK_REFRESHED', data: { lock }, request_id: current().lastSent().request_id as string })
    await expect(refresh).resolves.toEqual(lock)

    const expired = client.refreshLock('L2')
    current().receive({
      type: 'ERROR',
      data: { code: 'LOCK_NOT_FOUND', message: 'Esta seleção expirou.' },
      request_id: current().lastSent().request_id as string,
    })
    await expect(expired).resolves.toBeNull()

    client.unlock('L1')
    expect(current().lastSent()).toMatchObject({ type: 'UNLOCK_SLOT', data: { lock_id: 'L1' } })
  })

  it('aceita resposta sem request_id casando com o pedido mais antigo do mesmo tipo', async () => {
    const { client } = setup()
    client.start()
    current().open()
    current().hello()
    const pending = client.lockSlot({ room_id: 1, date: '2026-10-26', start: '08:00', end: '08:30' })
    const lock = makeLock()
    current().receive({ type: 'LOCK_ACQUIRED', data: { lock } })
    await expect(pending).resolves.toEqual({ ok: true, lock })
  })

  it('ignora mensagens que não são JSON', () => {
    const { client, events } = setup()
    client.start()
    current().open()
    current().onmessage?.(new MessageEvent('message', { data: 'não é json' }))
    expect(events).toEqual([])
  })
})
