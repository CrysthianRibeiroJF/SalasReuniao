import { create } from 'zustand'

import type { ConnectionStatus } from '../realtime/socket'

interface RealtimeState {
  status: ConnectionStatus
  /** Diferença (ms) entre o relógio do servidor e o local, medida no HELLO. */
  clockSkewMs: number
  /** Sem WebSocket há um tempo: a grade se atualiza por consultas periódicas (proxy que bloqueia /ws). */
  polling: boolean
  setPolling: (polling: boolean) => void
  setStatus: (status: ConnectionStatus) => void
  setServerTime: (serverTimeIso: string) => void
  reset: () => void
}

export const useRealtime = create<RealtimeState>()((set) => ({
  status: 'idle',
  clockSkewMs: 0,
  polling: false,
  setPolling: (polling) => set({ polling }),
  setStatus: (status) => set({ status }),
  setServerTime: (serverTimeIso) => {
    const server = Date.parse(serverTimeIso)
    if (!Number.isNaN(server)) set({ clockSkewMs: server - Date.now() })
  },
  reset: () => set({ status: 'idle', clockSkewMs: 0, polling: false }),
}))

/** "Agora" pelo relógio do servidor (para expiração de travas). */
export function serverNow(): number {
  return Date.now() + useRealtime.getState().clockSkewMs
}
