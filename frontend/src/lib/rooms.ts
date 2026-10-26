import type { RoomOut } from '../api/types'

export type RoomFilter = 'all' | 'video' | 'normal' | 'free'

export const ROOM_FILTERS: readonly { value: RoomFilter; label: string; short: string }[] = [
  { value: 'all', label: 'Todas as Salas', short: 'Todas' },
  { value: 'video', label: 'Videoconferência', short: 'Vídeo' },
  { value: 'normal', label: 'Salas Normais', short: 'Normais' },
  // Mesmo nome do plano/manual: não é mais longo que uma abreviação (R33).
  { value: 'free', label: 'Livres Dia Inteiro', short: 'Livres Dia Inteiro' },
]

const collator = new Intl.Collator('pt-BR', { numeric: true, sensitivity: 'base' })

/** Mesma ordem do backend: effective_type VIDEO primeiro, depois display_order e nome. */
export function compareRooms(a: RoomOut, b: RoomOut): number {
  if (a.effective_type !== b.effective_type) return a.effective_type === 'VIDEO' ? -1 : 1
  if (a.display_order !== b.display_order) return a.display_order - b.display_order
  return collator.compare(a.name, b.name)
}

export function sortRooms(rooms: Iterable<RoomOut>): RoomOut[] {
  return [...rooms].sort(compareRooms)
}

/** Salas ativas, ordenadas (as que aceitam reservas novas). */
export function gridRooms(rooms: Iterable<RoomOut>): RoomOut[] {
  return sortRooms([...rooms].filter((r) => r.is_active))
}

/**
 * Colunas da grade num dia: as salas ativas e, só nos dias em que ainda têm reservas ativas (dados
 * antigos), as desativadas — assim grade, contagem das abas e busca mostram as mesmas reservas (D1).
 */
export function gridRoomsForDay(rooms: Iterable<RoomOut>, busyRoomIds: ReadonlySet<number>): RoomOut[] {
  return sortRooms([...rooms].filter((r) => r.is_active || busyRoomIds.has(r.id)))
}

/** A sala comporta a reunião? Capacidade 0 = não informada; sem participantes informados, sempre cabe. */
export function fitsAttendees(room: Pick<RoomOut, 'capacity'>, attendees: number | null | undefined): boolean {
  return !attendees || room.capacity <= 0 || room.capacity >= attendees
}

/** Salas que comportam a reunião primeiro (ordem original mantida dentro de cada grupo). */
export function preferFitting<T extends Pick<RoomOut, 'capacity'>>(
  rooms: readonly T[],
  attendees: number | null | undefined,
): T[] {
  if (!attendees) return [...rooms]
  return [...rooms].sort((a, b) => Number(fitsAttendees(b, attendees)) - Number(fitsAttendees(a, attendees)))
}

/** "6 lugares" / "1 lugar"; null quando a capacidade não foi informada. */
export function seatsLabel(capacity: number): string | null {
  if (capacity <= 0) return null
  return capacity === 1 ? '1 lugar' : `${capacity} lugares`
}

export function filterRooms(
  rooms: readonly RoomOut[],
  filter: RoomFilter,
  busyRoomIds: ReadonlySet<number>,
): RoomOut[] {
  switch (filter) {
    case 'video':
      return rooms.filter((r) => r.effective_type === 'VIDEO')
    case 'normal':
      return rooms.filter((r) => r.effective_type === 'NORMAL')
    case 'free':
      return rooms.filter((r) => !busyRoomIds.has(r.id))
    default:
      return [...rooms]
  }
}

export interface RoomGroups {
  video: RoomOut[]
  normal: RoomOut[]
}

/** Sala VIDEO em manutenção cai no bloco das normais (RF02). */
export function groupRooms(rooms: readonly RoomOut[]): RoomGroups {
  return {
    video: rooms.filter((r) => r.effective_type === 'VIDEO'),
    normal: rooms.filter((r) => r.effective_type !== 'VIDEO'),
  }
}

export function isInMaintenance(room: Pick<RoomOut, 'room_type' | 'video_status'>): boolean {
  return room.room_type === 'VIDEO' && room.video_status === 'MAINTENANCE'
}

/** "na Sala 3" / "em Auditório" — preposição para frases como "Reservar na Sala 3". */
export function inRoom(name: string): string {
  return /^sala\b/i.test(name.trim()) ? `na ${name}` : `em ${name}`
}
