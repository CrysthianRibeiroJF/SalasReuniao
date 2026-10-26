import { describe, expect, it } from 'vitest'

import { makeBooking } from '../../test/fixtures'
import { firstPastIndex, moveActiveIndex, planSearchNavigation } from './searchNav'

describe('moveActiveIndex', () => {
  it('navega de forma circular', () => {
    expect(moveActiveIndex(0, 1, 3)).toBe(1)
    expect(moveActiveIndex(2, 1, 3)).toBe(0)
    expect(moveActiveIndex(0, -1, 3)).toBe(2)
    expect(moveActiveIndex(-1, 1, 3)).toBe(0)
    expect(moveActiveIndex(-1, -1, 3)).toBe(2)
    expect(moveActiveIndex(0, 1, 0)).toBe(-1)
  })
})

describe('firstPastIndex', () => {
  it('acha onde começam as reservas anteriores a hoje', () => {
    const list = [
      makeBooking({ date: '2026-10-26' }),
      makeBooking({ date: '2026-11-02' }),
      makeBooking({ date: '2026-10-20' }),
    ]
    expect(firstPastIndex(list, '2026-10-26')).toBe(2)
    expect(firstPastIndex(list.slice(0, 2), '2026-10-26')).toBe(-1)
  })
})

describe('planSearchNavigation', () => {
  const booking = makeBooking({ id: 'b9', date: '2026-10-29', room_id: 4 })

  it('vai para o dia da reserva e destaca', () => {
    expect(planSearchNavigation(booking, 'all', { effective_type: 'VIDEO' })).toEqual({
      date: '2026-10-29',
      highlightId: 'b9',
      resetFilter: false,
    })
  })

  it('limpa o filtro quando ele esconderia a sala', () => {
    expect(planSearchNavigation(booking, 'normal', { effective_type: 'VIDEO' }).resetFilter).toBe(true)
    expect(planSearchNavigation(booking, 'video', { effective_type: 'VIDEO' }).resetFilter).toBe(false)
    expect(planSearchNavigation(booking, 'video', { effective_type: 'NORMAL' }).resetFilter).toBe(true)
    // "Livres Dia Inteiro" nunca mostra uma sala com reserva
    expect(planSearchNavigation(booking, 'free', { effective_type: 'NORMAL' }).resetFilter).toBe(true)
  })
})
