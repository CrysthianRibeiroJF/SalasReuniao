import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BookingOut } from '../../api/types'
import { useBookings } from '../../store/bookings'
import { useConfig } from '../../store/config'
import { useUi } from '../../store/ui'
import { CONFIG, makeBooking } from '../../test/fixtures'
import { BookingHistory, BookingPopoverHost } from './BookingPopover'

vi.mock('../../api/endpoints', () => ({
  api: {
    bookings: {
      setStatus: vi.fn(),
      cancel: vi.fn(),
      logs: vi.fn(),
      listDay: vi.fn(async () => []),
      listRange: vi.fn(async () => []),
    },
  },
}))

const { api } = await import('../../api/endpoints')

const ANCHOR = { top: 100, left: 100, width: 120, height: 40 }

beforeEach(() => {
  vi.clearAllMocks()
  useConfig.setState({ config: CONFIG, status: 'ready', error: null })
  useUi.getState().reset()
  useBookings.getState().reset()
})

describe('BookingPopover', () => {
  it('R39: check-in que termina depois de abrir outra reserva não fecha o popover novo', async () => {
    const user = userEvent.setup()
    const a = makeBooking({ id: 'a', requester_name: 'Reserva A' })
    const b = makeBooking({ id: 'b', requester_name: 'Reserva B', start: '16:00', end: '17:00' })
    useBookings.getState().upsert(a)
    useBookings.getState().upsert(b)
    let finish!: (value: BookingOut) => void
    vi.mocked(api.bookings.setStatus).mockReturnValue(new Promise((resolve) => (finish = resolve)))
    render(<BookingPopoverHost />)
    act(() => useUi.getState().openPopover({ bookingId: 'a', anchor: ANCHOR }))
    await user.click(screen.getByRole('button', { name: 'Check-in' }))
    act(() => useUi.getState().openPopover({ bookingId: 'b', anchor: ANCHOR })) // clique na reserva B
    await act(async () => finish({ ...a, status: 'CHECKED_IN', version: 2 }))
    expect(useUi.getState().popover?.bookingId).toBe('b')
    expect(screen.getByRole('dialog', { name: 'Reserva B' })).toBeInTheDocument()
  })

  it('R31: o histórico mostra o que mudou junto com a troca de sala', async () => {
    vi.mocked(api.bookings.logs).mockResolvedValue([
      {
        id: 1,
        booking_id: 'x',
        user: { id: 2, full_name: 'Beatriz Santos' },
        action: 'MOVED_ROOM',
        previous_state: {
          room_name: 'Sala 01',
          start: '14:00',
          end: '15:00',
          ticket_number: '111',
          requester_name: 'Ana',
        },
        new_state: {
          room_name: 'Sala 03',
          start: '15:00',
          end: '16:00',
          ticket_number: '222',
          requester_name: 'Bruno',
        },
        created_at: '2026-10-26T14:03:00-03:00',
      },
    ])
    render(<BookingHistory bookingId="x" timeZone="America/Sao_Paulo" />)
    expect(await screen.findByText('Sala 01 → Sala 03')).toBeInTheDocument()
    const details = [...document.querySelectorAll('ol ul li')].map((li) => li.textContent)
    // a sala já está no resumo; o resto que mudou na mesma edição aparece embaixo
    expect(details).toEqual([
      'Início: 14:00 → 15:00',
      'Fim: 15:00 → 16:00',
      'Chamado: #111 → #222',
      'Solicitante: Ana → Bruno',
    ])
  })
})
