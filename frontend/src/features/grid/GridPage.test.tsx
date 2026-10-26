import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { LockOut } from '../../api/types'
import { useAuth } from '../../store/auth'
import { useBookings } from '../../store/bookings'
import { useConfig } from '../../store/config'
import { useLocks } from '../../store/locks'
import { useRealtime } from '../../store/realtime'
import { useRooms } from '../../store/rooms'
import { useUi } from '../../store/ui'
import { ADMIN, CONFIG, makeBooking, makeLock, makeRoom } from '../../test/fixtures'
import { GridPage } from './GridPage'

vi.mock('../../api/endpoints', () => ({
  api: {
    bookings: {
      listDay: vi.fn(async () => []),
      listRange: vi.fn(async () => []),
      search: vi.fn(async () => []),
      create: vi.fn(),
      update: vi.fn(),
    },
    availability: { freeRooms: vi.fn(async () => []) },
  },
}))

type LockReply = { ok: true; lock: LockOut }
const lockReplies: ((reply: LockReply) => void)[] = []
const realtime = {
  isOpen: true,
  lockSlot: vi.fn(() => new Promise<LockReply>((resolve) => lockReplies.push(resolve))),
  unlock: vi.fn(),
  refreshLock: vi.fn(async () => null),
}
vi.mock('../../realtime/service', () => ({
  getRealtime: () => realtime,
  onRealtimeEvent: () => () => {},
}))

const { api } = await import('../../api/endpoints')

const DATE = '2030-03-04'

function Probe() {
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <>
      <p data-testid="where">{`${location.pathname}${location.search}`}</p>
      <button type="button" onClick={() => navigate('/admin')}>
        Ir para a administração
      </button>
      <button type="button" onClick={() => navigate(-1)}>
        Voltar
      </button>
    </>
  )
}

function renderPage(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Probe />
      <Routes>
        <Route path="/" element={<GridPage />} />
        <Route path="/admin" element={<p>administração</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  lockReplies.length = 0
  useConfig.setState({ config: CONFIG, status: 'ready', error: null })
  useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
  useRooms.setState({
    byId: { 5: makeRoom({ id: 5, name: 'Sala 05' }), 6: makeRoom({ id: 6, name: 'Sala 06', display_order: 2 }) },
    status: 'ready',
    error: null,
  })
  useUi.getState().reset()
  useLocks.getState().reset()
  useBookings.getState().reset()
  useRealtime.setState({ status: 'open' })
  const v1 = makeBooking({ id: 'v1', room_id: 6, date: DATE, start: '09:00', end: '10:00' })
  useBookings.getState().upsert(v1)
  vi.mocked(api.bookings.listRange).mockResolvedValue([v1])
})

describe('GridPage', () => {
  it('R8: sair da grade com o modal aberto encerra a tentativa; voltar não traz o modal sem trava', async () => {
    const user = userEvent.setup()
    renderPage(`/?date=${DATE}`)
    act(() =>
      useUi.getState().openComposer({
        mode: 'create',
        bookingId: null,
        roomId: 5,
        date: DATE,
        period: 'CUSTOM',
        start: '14:00',
        end: '14:30',
        lockId: 'L1',
        anchor: null,
      }),
    )
    expect(await screen.findByRole('dialog', { name: 'Nova reserva' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Ir para a administração' }))
    expect(realtime.unlock).toHaveBeenCalledWith('L1')
    expect(useUi.getState().composer).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Voltar' }))
    await screen.findByRole('grid')
    expect(screen.queryByRole('dialog', { name: 'Nova reserva' })).not.toBeInTheDocument()
  })

  it('R32: Esc antes da resposta da trava cancela — o modal não abre e a trava que chegar é liberada', async () => {
    const user = userEvent.setup()
    renderPage(`/?date=${DATE}`)
    await screen.findByRole('grid')
    await user.keyboard('{ArrowDown}{Enter}') // cursor na 1ª sala às 08:00 → Enter pede a trava
    await waitFor(() => expect(realtime.lockSlot).toHaveBeenCalledOnce())
    await user.keyboard('{Escape}')
    await act(async () =>
      lockReplies[0]!({ ok: true, lock: makeLock({ lock_id: 'late', room_id: 5, start: '08:00', end: '08:30' }) }),
    )
    expect(useUi.getState().composer).toBeNull()
    expect(useUi.getState().selection).toBeNull()
    expect(realtime.unlock).toHaveBeenCalledWith('late')
  })

  it('D1: ?highlight=<id> (link da administração) destaca a reserva no dia e sai da URL', async () => {
    renderPage(`/?date=${DATE}&highlight=v1`)
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(`/?date=${DATE}`))
    expect(screen.getByTestId('where').textContent).not.toContain('highlight')
    expect(useUi.getState().highlightId).toBe('v1')
    expect(document.querySelector('[data-booking-id="v1"]')).toHaveClass('animate-highlight')
  })
})
