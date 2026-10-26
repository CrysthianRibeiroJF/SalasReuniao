import { describe, expect, it } from 'vitest'

import { makeRoom } from '../test/fixtures'
import { initials, userColor } from './people'
import {
  compareRooms,
  filterRooms,
  fitsAttendees,
  gridRooms,
  gridRoomsForDay,
  groupRooms,
  inRoom,
  isInMaintenance,
  preferFitting,
  ROOM_FILTERS,
  seatsLabel,
} from './rooms'

const rooms = [
  makeRoom({ id: 1, name: 'Sala 10', room_type: 'NORMAL', display_order: 2 }),
  makeRoom({ id: 2, name: 'Sala 2', room_type: 'VIDEO', display_order: 1 }),
  makeRoom({ id: 3, name: 'Sala 3', room_type: 'VIDEO', display_order: 2, video_status: 'MAINTENANCE' }),
  makeRoom({ id: 4, name: 'Sala 9', room_type: 'NORMAL', display_order: 2 }),
  makeRoom({ id: 5, name: 'Sala Velha', room_type: 'NORMAL', display_order: 0, is_active: false }),
]

describe('salas', () => {
  it('ordena como o backend: vídeo efetivo, ordem, nome (numérico)', () => {
    expect([...rooms].sort(compareRooms).map((r) => r.id)).toEqual([2, 5, 3, 4, 1])
    expect(gridRooms(rooms).map((r) => r.id)).toEqual([2, 3, 4, 1])
  })

  it('sala de vídeo em manutenção conta como normal', () => {
    const active = gridRooms(rooms)
    expect(groupRooms(active).video.map((r) => r.id)).toEqual([2])
    expect(groupRooms(active).normal.map((r) => r.id)).toEqual([3, 4, 1])
    expect(isInMaintenance(rooms[2]!)).toBe(true)
    expect(isInMaintenance(rooms[1]!)).toBe(false)
  })

  it('filtros de 1 clique', () => {
    const active = gridRooms(rooms)
    expect(filterRooms(active, 'all', new Set()).map((r) => r.id)).toEqual([2, 3, 4, 1])
    expect(filterRooms(active, 'video', new Set()).map((r) => r.id)).toEqual([2])
    expect(filterRooms(active, 'normal', new Set()).map((r) => r.id)).toEqual([3, 4, 1])
    expect(filterRooms(active, 'free', new Set([2, 4])).map((r) => r.id)).toEqual([3, 1])
  })

  it('preposição para frases', () => {
    expect(inRoom('Sala 05')).toBe('na Sala 05')
    expect(inRoom('Auditório')).toBe('em Auditório')
  })
})

describe('pessoas', () => {
  it('iniciais ignoram preposições', () => {
    expect(initials('Ana Paula Ribeiro')).toBe('AR')
    expect(initials('Maria da Silva')).toBe('MS')
    expect(initials('Beatriz')).toBe('BE')
    expect(initials('  ')).toBe('?')
  })

  it('cor é determinística por id', () => {
    expect(userColor(7)).toBe(userColor(7))
    expect(new Set([1, 2, 3, 4, 5].map(userColor)).size).toBeGreaterThan(1)
    expect(userColor(3)).toMatch(/^#[0-9a-f]{6}$/)
  })
})

describe('salas: desativadas, capacidade e rótulos', () => {
  it('D1: gridRoomsForDay inclui a sala desativada só quando ela tem reservas no dia', () => {
    expect(gridRoomsForDay(rooms, new Set()).map((r) => r.id)).toEqual([2, 3, 4, 1])
    expect(gridRoomsForDay(rooms, new Set([5])).map((r) => r.id)).toEqual([2, 5, 3, 4, 1])
  })

  it('R12: preferFitting põe primeiro as salas que comportam a reunião (ordem estável)', () => {
    const list = [makeRoom({ id: 1, capacity: 4 }), makeRoom({ id: 2, capacity: 0 }), makeRoom({ id: 3, capacity: 40 })]
    expect(preferFitting(list, 12).map((r) => r.id)).toEqual([2, 3, 1])
    expect(preferFitting(list, null).map((r) => r.id)).toEqual([1, 2, 3])
    expect(fitsAttendees(list[0]!, 4)).toBe(true)
    expect(fitsAttendees(list[0]!, 5)).toBe(false)
    expect(seatsLabel(1)).toBe('1 lugar')
    expect(seatsLabel(6)).toBe('6 lugares')
    expect(seatsLabel(0)).toBeNull()
  })

  it('R33: o filtro Livres Dia Inteiro usa o nome do plano também na forma curta', () => {
    expect(ROOM_FILTERS.find((f) => f.value === 'free')).toMatchObject({
      label: 'Livres Dia Inteiro',
      short: 'Livres Dia Inteiro',
    })
  })
})
