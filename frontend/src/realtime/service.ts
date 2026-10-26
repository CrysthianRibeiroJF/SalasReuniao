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
import { buildSocketUrl, RealtimeClient } from './socket'

let client: RealtimeClient | null = null
let sweepTimer: ReturnType<typeof setInterval> | undefined
/** Token usado na conexão atual (para saber se a sessão mudou desde então). */
let socketToken: string | null = null
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
    onStatus: (status) => useRealtime.getState().setStatus(status),
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
  clearInterval(sweepTimer)
  client?.stop()
  client = null
  socketToken = null
}

export function getRealtime(): RealtimeClient | null {
  return client
}
