import { create } from 'zustand'

import type { ConnectionStatus } from '../realtime/socket'

interface RealtimeState {
  status: ConnectionStatus
  /** Diferença (ms) entre o relógio do servidor e o local, medida no HELLO. */
  clockSkewMs: number
  setStatus: (status: ConnectionStatus) => void
  setServerTime: (serverTimeIso: string) => void
  reset: () => void
}

export const useRealtime = create<RealtimeState>()((set) => ({
  status: 'idle',
  clockSkewMs: 0,
  setStatus: (status) => set({ status }),
  setServerTime: (serverTimeIso) => {
    const server = Date.parse(serverTimeIso)
    if (!Number.isNaN(server)) set({ clockSkewMs: server - Date.now() })
  },
  reset: () => set({ status: 'idle', clockSkewMs: 0 }),
}))

/** "Agora" pelo relógio do servidor (para expiração de travas). */
export function serverNow(): number {
  return Date.now() + useRealtime.getState().clockSkewMs
}
