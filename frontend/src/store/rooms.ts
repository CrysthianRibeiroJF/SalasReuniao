import { useMemo } from 'react'
import { create } from 'zustand'

import { errorMessage } from '../api/errors'
import { api } from '../api/endpoints'
import type { RoomOut } from '../api/types'
import { gridRooms, sortRooms } from '../lib/rooms'

interface RoomsState {
  /** Todas as salas, inclusive inativas (a grade filtra as ativas). */
  byId: Record<number, RoomOut>
  /** 'error' = nunca carregou (uma falha numa recarga mantém 'ready' e a lista anterior). */
  status: 'idle' | 'loading' | 'ready' | 'error'
  error: string | null
  load: () => Promise<void>
  upsert: (room: RoomOut) => void
  reset: () => void
}

/**
 * Relógio lógico das salas: cada busca guarda o seq em que começou e cada sala recebida por evento
 * (ou resposta de escrita) o seq em que chegou. Uma resposta nunca desfaz uma sala recebida depois
 * do início da busca, e uma busca mais velha nunca sobrescreve uma mais nova (R25).
 */
let seq = 0
let appliedLoadSeq = 0
const upsertedAt = new Map<number, number>()

export const useRooms = create<RoomsState>()((set, get) => ({
  byId: {},
  status: 'idle',
  error: null,
  load: async () => {
    const started = ++seq
    set((s) => ({ status: s.status === 'ready' ? 'ready' : 'loading', error: null }))
    try {
      const rooms = await api.rooms.list(true)
      if (started < appliedLoadSeq) return
      appliedLoadSeq = started
      set((s) => {
        const byId: Record<number, RoomOut> = Object.fromEntries(rooms.map((r) => [r.id, r]))
        for (const [id, at] of upsertedAt) {
          const newer = s.byId[id]
          if (at > started && newer) byId[id] = newer
        }
        return { byId, status: 'ready', error: null }
      })
    } catch (error) {
      if (started < appliedLoadSeq) return
      set((s) => ({
        status: s.status === 'ready' ? 'ready' : 'error',
        error: errorMessage(error, 'Não foi possível carregar as salas.'),
      }))
    }
  },
  upsert: (room) => {
    upsertedAt.set(room.id, ++seq)
    set((s) => ({ byId: { ...s.byId, [room.id]: room } }))
    // A lista inicial falhou: uma sala avulsa não pode virar uma grade incompleta; busca tudo de novo.
    if (get().status === 'error') void get().load()
  },
  reset: () => {
    upsertedAt.clear()
    // Uma busca da sessão anterior que ainda volte depois disto é descartada.
    appliedLoadSeq = ++seq
    set({ byId: {}, status: 'idle', error: null })
  },
}))

/** Salas ativas na ordem da grade. */
export function useGridRooms(): RoomOut[] {
  const byId = useRooms((s) => s.byId)
  return useMemo(() => gridRooms(Object.values(byId)), [byId])
}

export function useAllRooms(): RoomOut[] {
  const byId = useRooms((s) => s.byId)
  return useMemo(() => sortRooms(Object.values(byId)), [byId])
}
