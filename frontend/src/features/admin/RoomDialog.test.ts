import { describe, expect, it } from 'vitest'

import { makeRoom } from '../../test/fixtures'
import { roomFormFrom, roomPatch } from './RoomDialog'

describe('formulário de sala', () => {
  const room = makeRoom({
    id: 3,
    name: 'Sala 03',
    room_type: 'VIDEO',
    capacity: 6,
    resources: ['TV'],
    display_order: 2,
  })

  it('carrega a sala (manutenção inclusa)', () => {
    expect(
      roomFormFrom(makeRoom({ room_type: 'VIDEO', video_status: 'MAINTENANCE', maintenance_ticket_id: 'INC1' })),
    ).toMatchObject({
      roomType: 'VIDEO',
      maintenance: true,
      maintenanceTicket: 'INC1',
    })
    expect(roomFormFrom(null)).toMatchObject({ name: '', roomType: 'NORMAL', isActive: true, resources: [] })
  })

  it('PATCH só com o que mudou', () => {
    const form = roomFormFrom(room)
    expect(roomPatch(room, form)).toEqual({})
    expect(roomPatch(room, { ...form, name: ' Sala 03B ', capacity: '8', resources: ['TV', 'Webcam'] })).toEqual({
      name: 'Sala 03B',
      capacity: 8,
      resources: ['TV', 'Webcam'],
    })
    expect(roomPatch(room, { ...form, roomType: 'NORMAL', isActive: false, displayOrder: '5' })).toEqual({
      room_type: 'NORMAL',
      is_active: false,
      display_order: 5,
    })
    // ordem vazia não é enviada (o backend decide)
    expect(roomPatch(room, { ...form, displayOrder: '' })).toEqual({})
  })
})
