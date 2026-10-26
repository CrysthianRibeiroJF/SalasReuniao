import { useEffect, useMemo, useState } from 'react'

import { api } from '../../../api/endpoints'
import type { FreeRoomsQuery, RoomOut } from '../../../api/types'
import { sortRooms } from '../../../lib/rooms'

const DEBOUNCE_MS = 250

/** Salas livres para um intervalo (GET /api/availability/free-rooms), com debounce e cancelamento. */
export function useFreeRooms(
  query: FreeRoomsQuery | null,
  excludeRoomId?: number,
): { rooms: RoomOut[]; loading: boolean } {
  const key = query ? JSON.stringify(query) : null
  const [state, setState] = useState<{ key: string | null; rooms: RoomOut[] }>({ key: null, rooms: [] })

  useEffect(() => {
    if (!key) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      api.availability
        .freeRooms(JSON.parse(key) as FreeRoomsQuery, controller.signal)
        .then((rooms) => setState({ key, rooms }))
        .catch(() => {
          if (!controller.signal.aborted) setState({ key, rooms: [] })
        })
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [key])

  const rooms = useMemo(
    () => (key && state.key === key ? sortRooms(state.rooms.filter((r) => r.is_active && r.id !== excludeRoomId)) : []),
    [key, state, excludeRoomId],
  )
  return { rooms, loading: key !== null && state.key !== key }
}
