/**
 * Ciclo de vida do tempo real da sessão: uma conexão por aba, ligada aos stores.
 * Iniciado pelo AppShell depois do login e encerrado ao sair.
 */
import { api } from '../api/endpoints'
import type { ServerEvent } from '../api/types'
import { useAuth } from '../store/auth'
import { useBookings } from '../store/bookings'
import { useLocks } from '../store/locks'
import { useRealtime } from '../store/realtime'
import { useRooms } from '../store/rooms'
import { useUi } from '../store/ui'
import { applyServerEvent } from './applyEvent'
import { buildSocketUrl, type ConnectionStatus, RealtimeClient } from './socket'

let client: RealtimeClient | null = null
let sweepTimer: ReturnType<typeof setInterval> | undefined
/** Token usado na conexão atual (para saber se a sessão mudou desde então). */
let socketToken: string | null = null
/**
 * Plano B para redes cujo proxy bloqueia o WebSocket: se a conexão não ficar pronta em
 * FALLBACK_AFTER_MS (ou cair e não voltar), a grade passa a ser recarregada a cada POLL_INTERVAL_MS.
 * Sem tempo real não há "Reservando: Fulana", mas o banco continua impedindo reservas duplicadas.
 */
const FALLBACK_AFTER_MS = 8_000
const POLL_INTERVAL_MS = 5_000
let fallbackTimer: ReturnType<typeof setTimeout> | undefined
let pollTimer: ReturnType<typeof setInterval> | undefined

function stopPolling(): void {
  clearTimeout(fallbackTimer)
  fallbackTimer = undefined
  clearInterval(pollTimer)
  pollTimer = undefined
  if (useRealtime.getState().polling) useRealtime.getState().setPolling(false)
}

function handleStatus(status: ConnectionStatus): void {
  useRealtime.getState().setStatus(status)
  if (status === 'open') {
    stopPolling()
    return
  }
  if (status === 'closed' || status === 'idle') {
    stopPolling()
    return
  }
  if (fallbackTimer || pollTimer) return
  fallbackTimer = setTimeout(() => {
    fallbackTimer = undefined
    useRealtime.getState().setPolling(true)
    resync()
    pollTimer = setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') resync()
    }, POLL_INTERVAL_MS)
  }, FALLBACK_AFTER_MS)
}

const listeners = new Set<(event: ServerEvent) => void>()

/**
 * Recarrega o que pode ter mudado fora da conexão: salas e a semana exibida.
 *
 * Roda em todo HELLO, inclusive o primeiro: uma busca (de salas ou da semana) iniciada antes de o
 * servidor registrar a conexão pode ter lido o banco antes de um evento que este socket não recebeu
 * (R25). A mescla por `seq` dos stores impede que a resposta mais velha desfaça eventos mais novos.
 */
export function resync(): void {
  void useRooms.getState().load()
  const date = useUi.getState().activeDate
  if (date) void useBookings.getState().loadWeek(date)
}

/** Ouve os eventos do servidor (depois de aplicados nos stores). Devolve a função que desliga. */
export function onRealtimeEvent(listener: (event: ServerEvent) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function dispatch(event: ServerEvent): void {
  applyServerEvent(event)
  for (const listener of listeners) listener(event)
}

/**
 * Fechamento 4401 (token inválido, vencido ou revogado). Se outra aba já trocou o token, só
 * reconecta com o novo; senão pergunta ao servidor o motivo exato — o 401 de /auth/me encerra a
 * sessão com "senha alterada" ou "acesso desativado" quando for o caso — e sai como "expirada".
 */
export async function handleSocketRejected(): Promise<void> {
  const token = useAuth.getState().token
  if (!token) return
  if (token !== socketToken) {
    stopRealtime()
    startRealtime()
    return
  }
  try {
    await api.auth.me()
  } catch {
    // o cliente HTTP já encerrou a sessão com o motivo do 401 (ou o servidor está fora do ar)
  }
  if (useAuth.getState().token === token) useAuth.getState().logout('expired')
}

export function startRealtime(): RealtimeClient {
  if (client) return client
  client = new RealtimeClient({
    url: () => {
      socketToken = useAuth.getState().token
      return buildSocketUrl(socketToken ?? '')
    },
    onEvent: dispatch,
    onStatus: handleStatus,
    onUnauthorized: () => void handleSocketRejected(),
    onForbidden: () => useAuth.getState().logout('disabled'),
    onReady: () => resync(),
  })
  client.start()
  clearInterval(sweepTimer)
  sweepTimer = setInterval(() => useLocks.getState().prune(), 1_000)
  return client
}

export function stopRealtime(): void {
  stopPolling()
  clearInterval(sweepTimer)
  client?.stop()
  client = null
  socketToken = null
}

export function getRealtime(): RealtimeClient | null {
  return client
}
