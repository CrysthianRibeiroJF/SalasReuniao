/**
 * Cliente WebSocket do tempo real (contrato §6).
 *
 * - Conecta em /ws?token=…; o servidor responde HELLO (estado inicial: travas e presença).
 * - Reconecta sozinho com backoff exponencial (até ~10 s) + jitter; fechamento 4401 = sessão inválida
 *   (token inválido, vencido ou revogado) e 4403 = usuário desativado: nesses dois não reconecta.
 * - PING a cada 25 s; sem PONG em 10 s a conexão é considerada morta e é refeita.
 * - O navegador avisou que a rede caiu (evento `offline`): fecha na hora e mostra "Reconectando…"
 *   (sem esperar o heartbeat); a rede voltou (`online`): reconecta já, sem esperar o backoff.
 * - LOCK_SLOT/REFRESH_LOCK são correlacionados à resposta por `request_id`, com timeout.
 */
import type { ClientMessageData, ClientMessageType, LockOut, LockSlotData, ServerEvent } from '../api/types'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

export const CLOSE_UNAUTHORIZED = 4401
export const CLOSE_FORBIDDEN = 4403
const CLOSE_PONG_TIMEOUT = 4000
const CLOSE_OFFLINE = 4002
const OPEN = 1

/** Subconjunto de WebSocket usado aqui (permite um falso nos testes). */
export interface SocketLike {
  readonly readyState: number
  onopen: ((event: Event) => void) | null
  onmessage: ((event: MessageEvent) => void) | null
  onclose: ((event: CloseEvent) => void) | null
  onerror: ((event: Event) => void) | null
  send(data: string): void
  close(code?: number, reason?: string): void
}

export type SocketFactory = (url: string) => SocketLike

/** Avisos do navegador de que a rede caiu ou voltou (permite um falso nos testes). */
export interface Connectivity {
  subscribe(listener: (online: boolean) => void): () => void
}

/** Eventos `online`/`offline` da janela (null fora do navegador). */
export const browserConnectivity: Connectivity | null =
  typeof window === 'undefined'
    ? null
    : {
        subscribe(listener) {
          const onOnline = () => listener(true)
          const onOffline = () => listener(false)
          window.addEventListener('online', onOnline)
          window.addEventListener('offline', onOffline)
          return () => {
            window.removeEventListener('online', onOnline)
            window.removeEventListener('offline', onOffline)
          }
        },
      }

export interface RealtimeClientOptions {
  /** URL completa, chamada a cada tentativa (o token pode ter mudado). */
  url: () => string
  onEvent: (event: ServerEvent) => void
  onStatus?: (status: ConnectionStatus) => void
  /** Servidor recusou o token (close 4401: inválido, vencido ou revogado). */
  onUnauthorized?: () => void
  /** Usuário desativado ou removido (close 4403). */
  onForbidden?: () => void
  /** Depois de cada HELLO; `reconnect` = já houve uma conexão antes desta. */
  onReady?: (info: { reconnect: boolean }) => void
  createSocket?: SocketFactory
  pingIntervalMs?: number
  pongTimeoutMs?: number
  requestTimeoutMs?: number
  baseBackoffMs?: number
  maxBackoffMs?: number
  random?: () => number
  /** Padrão: eventos online/offline do navegador; null desliga. */
  connectivity?: Connectivity | null
}

export type LockResult =
  | { ok: true; lock: LockOut }
  /** `code` = motivo do servidor: LOCKED_BY_OTHER, LOCK_BUSY, OUTSIDE_OPERATING_HOURS, UNAVAILABLE… */
  | { ok: false; reason: 'DENIED'; code: string; message: string; conflicts: LockOut[] }
  | { ok: false; reason: 'OFFLINE' | 'TIMEOUT' | 'ERROR'; code: string; message: string; conflicts: LockOut[] }

type PendingKind = 'lock' | 'refresh'

interface Pending {
  kind: PendingKind
  resolve: (event: ServerEvent | null) => void
  timer: ReturnType<typeof setTimeout>
}

/** Atraso da tentativa `attempt` (0, 1, 2…): 0,5 s · 1 s · 2 s · 4 s · 8 s · 10 s…, + até 30% (máx. 1 s) de jitter. */
export function backoffDelay(
  attempt: number,
  { base = 500, max = 10_000, random = Math.random }: { base?: number; max?: number; random?: () => number } = {},
): number {
  const exp = Math.min(max, base * 2 ** Math.max(0, attempt))
  return Math.round(exp + random() * Math.min(1_000, exp * 0.3))
}

/** ws(s)://host/ws?token=… a partir da origem atual (o build é servido pelo backend). */
export function buildSocketUrl(token: string, location: Pick<Location, 'protocol' | 'host'> = window.location): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}/ws?token=${encodeURIComponent(token)}`
}

const OFFLINE_MESSAGE = 'Sem conexão em tempo real no momento.'

export class RealtimeClient {
  private readonly options: Required<
    Omit<RealtimeClientOptions, 'onStatus' | 'onUnauthorized' | 'onForbidden' | 'onReady'>
  > &
    Pick<RealtimeClientOptions, 'onStatus' | 'onUnauthorized' | 'onForbidden' | 'onReady'>
  private socket: SocketLike | null = null
  private status: ConnectionStatus = 'idle'
  private stopped = true
  private attempt = 0
  private everReady = false
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined
  private unsubscribeConnectivity: (() => void) | undefined
  private pingTimer: ReturnType<typeof setInterval> | undefined
  private pongTimer: ReturnType<typeof setTimeout> | undefined
  private readonly pending = new Map<string, Pending>()
  private requestSeq = 0

  constructor(options: RealtimeClientOptions) {
    this.options = {
      createSocket: (url) => new WebSocket(url),
      pingIntervalMs: 25_000,
      pongTimeoutMs: 10_000,
      requestTimeoutMs: 6_000,
      baseBackoffMs: 500,
      maxBackoffMs: 10_000,
      random: Math.random,
      connectivity: browserConnectivity,
      ...options,
    }
  }

  get connectionStatus(): ConnectionStatus {
    return this.status
  }

  get isOpen(): boolean {
    return this.status === 'open'
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    this.attempt = 0
    this.unsubscribeConnectivity = this.options.connectivity?.subscribe((online) => this.handleConnectivity(online))
    this.open()
  }

  stop(): void {
    this.stopped = true
    this.unsubscribeConnectivity?.()
    this.unsubscribeConnectivity = undefined
    clearTimeout(this.reconnectTimer)
    this.stopHeartbeat()
    const socket = this.socket
    this.socket = null
    if (socket) {
      detach(socket)
      try {
        socket.close(1000, 'client stop')
      } catch {
        // já fechado
      }
    }
    this.failPending()
    this.setStatus('closed')
  }

  /** Envia uma mensagem; false se não houver conexão aberta. */
  send<T extends ClientMessageType>(type: T, data: ClientMessageData[T], requestId?: string): boolean {
    const socket = this.socket
    if (!socket || socket.readyState !== OPEN || this.status !== 'open') return false
    const envelope: Record<string, unknown> = { type, data, ts: new Date().toISOString() }
    if (requestId) envelope.request_id = requestId
    try {
      socket.send(JSON.stringify(envelope))
      return true
    } catch {
      return false
    }
  }

  async lockSlot(data: LockSlotData): Promise<LockResult> {
    const response = await this.request('LOCK_SLOT', data, 'lock')
    if (!response) {
      return this.isOpen
        ? {
            ok: false,
            reason: 'TIMEOUT',
            code: 'TIMEOUT',
            message: 'O servidor demorou a responder a trava.',
            conflicts: [],
          }
        : { ok: false, reason: 'OFFLINE', code: 'OFFLINE', message: OFFLINE_MESSAGE, conflicts: [] }
    }
    if (response.type === 'LOCK_ACQUIRED') return { ok: true, lock: response.data.lock }
    if (response.type === 'LOCK_DENIED') {
      return {
        ok: false,
        reason: 'DENIED',
        code: String(response.data.reason ?? 'LOCKED_BY_OTHER'),
        message: response.data.message,
        conflicts: response.data.conflicts ?? [],
      }
    }
    if (response.type === 'ERROR') {
      return { ok: false, reason: 'ERROR', code: response.data.code, message: response.data.message, conflicts: [] }
    }
    return {
      ok: false,
      reason: 'ERROR',
      code: 'UNEXPECTED',
      message: 'Resposta inesperada do servidor.',
      conflicts: [],
    }
  }

  /** Renova o TTL; devolve a trava renovada ou null (expirada, sem conexão, sem resposta). */
  async refreshLock(lockId: string): Promise<LockOut | null> {
    const response = await this.request('REFRESH_LOCK', { lock_id: lockId }, 'refresh')
    return response?.type === 'LOCK_REFRESHED' ? response.data.lock : null
  }

  unlock(lockId: string): void {
    this.send('UNLOCK_SLOT', { lock_id: lockId })
  }

  // ---------------------------------------------------------------- internos

  private request<T extends 'LOCK_SLOT' | 'REFRESH_LOCK'>(
    type: T,
    data: ClientMessageData[T],
    kind: PendingKind,
  ): Promise<ServerEvent | null> {
    const requestId = `${kind}-${++this.requestSeq}-${Date.now().toString(36)}`
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId)
        resolve(null)
      }, this.options.requestTimeoutMs)
      this.pending.set(requestId, { kind, resolve, timer })
      if (!this.send(type, data, requestId)) this.settle(requestId, null)
    })
  }

  private settle(requestId: string, event: ServerEvent | null): void {
    const pending = this.pending.get(requestId)
    if (!pending) return
    clearTimeout(pending.timer)
    this.pending.delete(requestId)
    pending.resolve(event)
  }

  private failPending(): void {
    for (const id of [...this.pending.keys()]) this.settle(id, null)
  }

  /** Resposta sem request_id (servidor antigo): casa com o pedido mais antigo do mesmo tipo. */
  private oldestPending(kind: PendingKind): string | null {
    for (const [id, pending] of this.pending) if (pending.kind === kind) return id
    return null
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) return
    this.status = status
    this.options.onStatus?.(status)
  }

  private open(): void {
    if (this.stopped) return
    this.setStatus(this.everReady ? 'reconnecting' : 'connecting')
    let socket: SocketLike
    try {
      socket = this.options.createSocket(this.options.url())
    } catch {
      this.scheduleReconnect()
      return
    }
    this.socket = socket
    socket.onopen = () => {
      // A conexão só é considerada pronta no HELLO; o heartbeat já começa aqui.
      this.startHeartbeat(socket)
    }
    socket.onmessage = (event) => this.handleMessage(event.data)
    socket.onerror = () => {
      // o onclose vem em seguida e decide a reconexão
    }
    socket.onclose = (event) => this.handleClose(socket, event.code)
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== 'string') return
    let event: ServerEvent
    try {
      event = JSON.parse(raw) as ServerEvent
    } catch {
      return
    }
    if (!event || typeof event.type !== 'string') return

    if (event.type === 'PONG') {
      clearTimeout(this.pongTimer)
      return
    }

    if (event.type === 'HELLO') {
      const reconnect = this.everReady
      this.everReady = true
      this.attempt = 0
      this.setStatus('open')
      this.options.onEvent(event)
      this.options.onReady?.({ reconnect })
      return
    }

    let requestId = event.request_id && this.pending.has(event.request_id) ? event.request_id : null
    if (!requestId && !event.request_id) {
      if (event.type === 'LOCK_ACQUIRED' || event.type === 'LOCK_DENIED') requestId = this.oldestPending('lock')
      else if (event.type === 'LOCK_REFRESHED') requestId = this.oldestPending('refresh')
    }
    if (requestId) this.settle(requestId, event)

    this.options.onEvent(event)
  }

  private handleClose(socket: SocketLike, code: number): void {
    if (socket !== this.socket) return
    detach(socket)
    this.socket = null
    this.stopHeartbeat()
    this.failPending()
    if (code === CLOSE_UNAUTHORIZED || code === CLOSE_FORBIDDEN) {
      // Sessão recusada: reconectar com o mesmo token não adianta.
      this.stopped = true
      this.setStatus('closed')
      if (code === CLOSE_FORBIDDEN) this.options.onForbidden?.()
      else this.options.onUnauthorized?.()
      return
    }
    if (!this.stopped) this.scheduleReconnect()
  }

  private handleConnectivity(online: boolean): void {
    if (this.stopped) return
    if (!online) {
      // Sem rede o socket pode continuar "aberto" sem entregar nada (o heartbeat só notaria em até
      // ~35 s): fecha já, para o indicador não mentir, e segue tentando com backoff.
      const socket = this.socket
      if (socket) {
        this.socket = null
        detach(socket)
        try {
          socket.close(CLOSE_OFFLINE, 'offline')
        } catch {
          // já fechado
        }
        this.stopHeartbeat()
        this.failPending()
      }
      this.scheduleReconnect()
      return
    }
    // A rede voltou: reconecta agora (a menos que já haja uma tentativa em andamento).
    if (this.socket) return
    clearTimeout(this.reconnectTimer)
    this.attempt = 0
    this.open()
  }

  private scheduleReconnect(): void {
    if (this.stopped) return
    this.setStatus(this.everReady ? 'reconnecting' : 'connecting')
    const delay = backoffDelay(this.attempt++, {
      base: this.options.baseBackoffMs,
      max: this.options.maxBackoffMs,
      random: this.options.random,
    })
    clearTimeout(this.reconnectTimer)
    this.reconnectTimer = setTimeout(() => this.open(), delay)
  }

  private startHeartbeat(socket: SocketLike): void {
    this.stopHeartbeat()
    this.pingTimer = setInterval(() => {
      if (socket !== this.socket || socket.readyState !== OPEN) return
      try {
        socket.send(JSON.stringify({ type: 'PING', data: {}, ts: new Date().toISOString() }))
      } catch {
        return
      }
      clearTimeout(this.pongTimer)
      this.pongTimer = setTimeout(() => {
        // Conexão "meio aberta" (rede caiu sem FIN): força o fechamento e reconecta.
        try {
          socket.close(CLOSE_PONG_TIMEOUT, 'pong timeout')
        } catch {
          // ignorado
        }
        this.handleClose(socket, CLOSE_PONG_TIMEOUT)
      }, this.options.pongTimeoutMs)
    }, this.options.pingIntervalMs)
  }

  private stopHeartbeat(): void {
    clearInterval(this.pingTimer)
    clearTimeout(this.pongTimer)
  }
}

function detach(socket: SocketLike): void {
  socket.onopen = null
  socket.onmessage = null
  socket.onerror = null
  socket.onclose = null
}
