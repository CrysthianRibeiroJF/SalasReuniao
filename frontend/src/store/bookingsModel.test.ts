import { describe, expect, it } from 'vitest'

import { makeBooking, makeRoom } from '../test/fixtures'
import {
  applyFetchedDates,
  applyRoomToBookings,
  bookingsForDate,
  countForDate,
  EMPTY_BOOKINGS,
  loadedDates,
  markError,
  markLoading,
  upsertBooking,
} from './bookingsModel'

describe('upsertBooking (eventos e respostas de escrita)', () => {
  it('insere uma reserva nova', () => {
    const b = makeBooking()
    const data = upsertBooking(EMPTY_BOOKINGS, b, 1)
    expect(data.byId[b.id]).toBe(b)
    expect(bookingsForDate(data.byId, b.date)).toEqual([b])
  })

  it('é idempotente: o mesmo evento duas vezes não muda nada', () => {
    const b = makeBooking()
    const once = upsertBooking(EMPTY_BOOKINGS, b, 1)
    const twice = upsertBooking(once, { ...b }, 2)
    expect(twice).toBe(once)
  })

  it('ignora versões menores (eventos fora de ordem)', () => {
    const v1 = makeBooking({ version: 1, start: '09:00', end: '10:00' })
    const v3 = { ...v1, version: 3, start: '11:00', end: '12:00' }
    const v2 = { ...v1, version: 2, start: '10:00', end: '11:00' }
    let data = upsertBooking(EMPTY_BOOKINGS, v1, 1)
    data = upsertBooking(data, v3, 2)
    data = upsertBooking(data, v2, 3) // chegou atrasado
    expect(data.byId[v1.id]?.version).toBe(3)
    expect(data.byId[v1.id]?.start).toBe('11:00')
  })

  it('reserva que muda de data sai do dia antigo', () => {
    const b = makeBooking({ date: '2026-10-26' })
    let data = upsertBooking(EMPTY_BOOKINGS, b, 1)
    data = upsertBooking(data, { ...b, version: 2, date: '2026-10-28' }, 2)
    expect(bookingsForDate(data.byId, '2026-10-26')).toEqual([])
    expect(bookingsForDate(data.byId, '2026-10-28').map((x) => x.id)).toEqual([b.id])
  })

  it('cancelada some da grade, mas bloqueia eventos atrasados (lápide)', () => {
    const b = makeBooking({ version: 1 })
    let data = upsertBooking(EMPTY_BOOKINGS, b, 1)
    data = upsertBooking(data, { ...b, version: 2, status: 'CANCELLED' }, 2)
    expect(bookingsForDate(data.byId, b.date)).toEqual([])
    data = upsertBooking(data, { ...b, version: 1, title: 'evento atrasado' }, 3)
    expect(bookingsForDate(data.byId, b.date)).toEqual([])
  })

  it('ordena o dia por início e sala', () => {
    const a = makeBooking({ start: '10:00', end: '11:00', room_id: 2 })
    const b = makeBooking({ start: '09:00', end: '10:00', room_id: 3 })
    const c = makeBooking({ start: '10:00', end: '10:30', room_id: 1 })
    let data = EMPTY_BOOKINGS
    for (const x of [a, b, c]) data = upsertBooking(data, x, 1)
    expect(bookingsForDate(data.byId, '2026-10-26').map((x) => x.id)).toEqual([b.id, c.id, a.id])
  })
})

describe('applyFetchedDates (busca de dias)', () => {
  it('marca o dia como carregado e conhece a contagem', () => {
    let data = markLoading(EMPTY_BOOKINGS, ['2026-10-26', '2026-10-27'], 10)
    expect(countForDate(data, '2026-10-26')).toBeNull()
    data = applyFetchedDates(data, ['2026-10-26', '2026-10-27'], [makeBooking(), makeBooking()], 10, 11)
    expect(data.dates['2026-10-26']?.status).toBe('loaded')
    expect(countForDate(data, '2026-10-26')).toBe(2)
    expect(countForDate(data, '2026-10-27')).toBe(0)
    expect(loadedDates(data).sort()).toEqual(['2026-10-26', '2026-10-27'])
  })

  it('descarta reservas que sumiram do dia (evento perdido durante a queda)', () => {
    const gone = makeBooking()
    const kept = makeBooking()
    let data = upsertBooking(EMPTY_BOOKINGS, gone, 1)
    data = upsertBooking(data, kept, 2)
    data = markLoading(data, ['2026-10-26'], 5)
    data = applyFetchedDates(data, ['2026-10-26'], [kept], 5, 6)
    expect(data.byId[gone.id]).toBeUndefined()
    expect(data.byId[kept.id]).toBeDefined()
  })

  it('preserva reservas recebidas por evento durante a busca', () => {
    let data = markLoading(EMPTY_BOOKINGS, ['2026-10-26'], 5)
    const created = makeBooking()
    data = upsertBooking(data, created, 6) // BOOKING_CREATED chegou antes da resposta
    data = applyFetchedDates(data, ['2026-10-26'], [], 5, 7)
    expect(data.byId[created.id]).toBe(created)
  })

  it('nunca rebaixa uma versão mais nova recebida por evento', () => {
    const b = makeBooking({ version: 1 })
    let data = markLoading(EMPTY_BOOKINGS, ['2026-10-26'], 5)
    data = upsertBooking(data, { ...b, version: 2, title: 'novo' }, 6)
    data = applyFetchedDates(data, ['2026-10-26'], [b], 5, 7)
    expect(data.byId[b.id]?.version).toBe(2)
    expect(data.byId[b.id]?.title).toBe('novo')
  })

  it('aceita a mesma versão vinda da busca (selos recalculados no servidor)', () => {
    const b = makeBooking({ version: 4, is_relocatable: false })
    let data = upsertBooking(EMPTY_BOOKINGS, b, 1)
    data = markLoading(data, ['2026-10-26'], 2)
    data = applyFetchedDates(data, ['2026-10-26'], [{ ...b, is_relocatable: true }], 2, 3)
    expect(data.byId[b.id]?.is_relocatable).toBe(true)
  })

  it('busca superada por outra mais nova não marca o dia como carregado nem apaga nada', () => {
    const old = makeBooking()
    let data = upsertBooking(EMPTY_BOOKINGS, old, 1)
    data = markLoading(data, ['2026-10-26'], 2)
    data = markLoading(data, ['2026-10-26'], 3)
    data = applyFetchedDates(data, ['2026-10-26'], [], 2, 4)
    expect(data.dates['2026-10-26']?.status).toBe('loading')
    expect(data.dates['2026-10-26']?.known).toBe(true)
    expect(data.byId[old.id]).toBeDefined()
  })

  it('erro só vale para a busca mais recente do dia', () => {
    let data = markLoading(EMPTY_BOOKINGS, ['2026-10-26'], 2)
    data = markLoading(data, ['2026-10-26'], 3)
    expect(markError(data, ['2026-10-26'], 2)).toBe(data)
    expect(markError(data, ['2026-10-26'], 3).dates['2026-10-26']?.status).toBe('error')
  })
})

describe('applyRoomToBookings (ROOM_UPDATED)', () => {
  it('recalcula selos quando a sala entra/sai de manutenção', () => {
    const presencial = makeBooking({ room_id: 7, requires_video: false, is_relocatable: true })
    const comVideo = makeBooking({ room_id: 7, requires_video: true, video_unavailable: false })
    const outraSala = makeBooking({ room_id: 8 })
    let data = EMPTY_BOOKINGS
    for (const b of [presencial, comVideo, outraSala]) data = upsertBooking(data, b, 1)

    const emManutencao = makeRoom({ id: 7, name: 'Sala 07', room_type: 'VIDEO', video_status: 'MAINTENANCE' })
    data = applyRoomToBookings(data, emManutencao)
    expect(data.byId[presencial.id]?.is_relocatable).toBe(false)
    expect(data.byId[comVideo.id]?.video_unavailable).toBe(true)
    expect(data.byId[outraSala.id]).toBe(outraSala)

    const consertada = makeRoom({ id: 7, name: 'Sala 07', room_type: 'VIDEO', video_status: 'OPERATIONAL' })
    data = applyRoomToBookings(data, consertada)
    expect(data.byId[presencial.id]?.is_relocatable).toBe(true)
    expect(data.byId[comVideo.id]?.video_unavailable).toBe(false)
  })

  it('não muda o estado quando nada muda', () => {
    const b = makeBooking({ room_id: 1, room_name: 'Sala 01' })
    const data = upsertBooking(EMPTY_BOOKINGS, b, 1)
    expect(applyRoomToBookings(data, makeRoom({ id: 1, name: 'Sala 01' }))).toBe(data)
  })

  it('propaga renomeação da sala', () => {
    const b = makeBooking({ room_id: 1, room_name: 'Sala 01' })
    const data = applyRoomToBookings(upsertBooking(EMPTY_BOOKINGS, b, 1), makeRoom({ id: 1, name: 'Sala Azul' }))
    expect(data.byId[b.id]?.room_name).toBe('Sala Azul')
    expect(data.byId[b.id]?.version).toBe(b.version)
  })
})
