import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../../api/errors'
import type { BookingWriteResult, RoomOut, ServerEvent } from '../../../api/types'
import { useBookings } from '../../../store/bookings'
import { bookingsForDate } from '../../../store/bookingsModel'
import { useConfig } from '../../../store/config'
import { useRealtime } from '../../../store/realtime'
import { useRooms } from '../../../store/rooms'
import { useToasts } from '../../../store/toasts'
import { useUi } from '../../../store/ui'
import { CONFIG, makeBooking, makeLock, makeRoom } from '../../../test/fixtures'
import { BookingComposerHost } from './BookingComposer'
import { refreshIntervalFor } from './useComposerLock'

vi.mock('../../../api/endpoints', () => ({
  api: {
    bookings: { create: vi.fn(), update: vi.fn() },
    availability: { freeRooms: vi.fn(async () => []) },
  },
}))

const realtime = {
  isOpen: true,
  lockSlot: vi.fn(),
  unlock: vi.fn(),
  refreshLock: vi.fn(async () => null),
}
const realtimeListeners = new Set<(event: ServerEvent) => void>()
vi.mock('../../../realtime/service', () => ({
  getRealtime: () => realtime,
  onRealtimeEvent: (listener: (event: ServerEvent) => void) => {
    realtimeListeners.add(listener)
    return () => realtimeListeners.delete(listener)
  },
}))

/** Simula um evento do servidor chegando pelo WebSocket. */
function serverEvent(event: ServerEvent) {
  act(() => {
    for (const listener of realtimeListeners) listener(event)
  })
}

function unlocked(lockId: string, reason: 'saved' | 'expired' | 'released' = 'saved'): ServerEvent {
  return {
    type: 'SLOT_UNLOCKED',
    data: { lock: makeLock({ lock_id: lockId, room_id: 5, user: { id: 1, full_name: 'Ana' } }), reason },
    ts: new Date().toISOString(),
  }
}

const { api } = await import('../../../api/endpoints')

const VIDEO_ROOM = makeRoom({ id: 1, name: 'Sala 01', room_type: 'VIDEO', capacity: 6, display_order: 1 })
const NORMAL_ROOM = makeRoom({ id: 5, name: 'Sala 05', room_type: 'NORMAL', capacity: 4, display_order: 1 })
const NORMAL_ROOM_2 = makeRoom({ id: 6, name: 'Sala 06', room_type: 'NORMAL', display_order: 2 })

function setRooms(rooms: RoomOut[]) {
  useRooms.setState({ byId: Object.fromEntries(rooms.map((r) => [r.id, r])), status: 'ready' })
}

function openComposer(roomId = NORMAL_ROOM.id, lockId: string | null = 'L1') {
  act(() => {
    useUi.getState().openComposer({
      mode: 'create',
      bookingId: null,
      roomId,
      date: '2026-10-26',
      period: 'CUSTOM',
      start: '14:00',
      end: '15:00',
      lockId,
      anchor: null,
    })
  })
}

function created(overrides: Parameters<typeof makeBooking>[0] = {}): BookingWriteResult {
  return { booking: makeBooking({ room_id: NORMAL_ROOM.id, room_name: 'Sala 05', ...overrides }), warnings: [] }
}

beforeEach(() => {
  vi.clearAllMocks()
  useConfig.setState({ config: CONFIG, status: 'ready', error: null })
  setRooms([VIDEO_ROOM, NORMAL_ROOM, NORMAL_ROOM_2])
  useBookings.getState().reset()
  useUi.getState().reset()
  useToasts.getState().clear()
  useRealtime.setState({ status: 'open' })
  realtime.lockSlot.mockImplementation(async (data: { room_id: number; start: string; end: string }) => ({
    ok: true,
    lock: makeLock({
      lock_id: `L-${data.room_id}-${data.start}-${data.end}`,
      room_id: data.room_id,
      start: data.start,
      end: data.end,
    }),
  }))
  vi.mocked(api.availability.freeRooms).mockResolvedValue([])
})

describe('BookingComposer (modal rápido)', () => {
  it('abre com foco no Nº do Chamado e mostra sala, dia e horário', () => {
    render(<BookingComposerHost />)
    openComposer()
    expect(screen.getByLabelText('Nº do Chamado')).toHaveFocus()
    expect(screen.getByRole('dialog', { name: 'Nova reserva' })).toHaveTextContent('Sala 05')
    expect(screen.getByRole('dialog')).toHaveTextContent('Seg 26/10')
    expect(screen.getByRole('dialog')).toHaveTextContent('14:00–15:00')
  })

  it('RNF03: chamado → Tab → solicitante → Enter salva com a trava', async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookings.create).mockResolvedValue(created())
    render(<BookingComposerHost />)
    openComposer()

    await user.keyboard('123456')
    await user.tab()
    expect(screen.getByLabelText(/Solicitante/)).toHaveFocus()
    await user.keyboard('Marcos Lima{Enter}')

    await waitFor(() => expect(api.bookings.create).toHaveBeenCalledOnce())
    expect(api.bookings.create).toHaveBeenCalledWith({
      room_id: 5,
      date: '2026-10-26',
      period_type: 'CUSTOM',
      start: '14:00',
      end: '15:00',
      ticket_number: '123456',
      title: null,
      requester_name: 'Marcos Lima',
      requires_video: false,
      attendees: null,
      notes: null,
      lock_id: 'L1',
    })
    await waitFor(() => expect(useUi.getState().composer).toBeNull())
    expect(bookingsForDate(useBookings.getState().data.byId, '2026-10-26')).toHaveLength(1)
    expect(useToasts.getState().toasts[0]).toMatchObject({ kind: 'success', title: 'Reserva criada' })
    // Salvar libera a trava no servidor: o cliente não manda UNLOCK
    expect(realtime.unlock).not.toHaveBeenCalled()
  })

  it('Alt+1..4 troca o tipo de período e atualiza a prévia da seleção', async () => {
    const user = userEvent.setup()
    render(<BookingComposerHost />)
    openComposer()

    await user.keyboard('{Alt>}2{/Alt}')
    expect(screen.getByRole('radio', { name: 'Dia Inteiro' })).toBeChecked()
    expect(useUi.getState().composer).toMatchObject({ period: 'FULL_DAY', start: '08:00', end: '19:00' })
    expect(screen.getByRole('dialog')).toHaveTextContent('08:00–19:00')
    expect(screen.queryByLabelText('Início')).not.toBeInTheDocument()

    await user.keyboard('{Alt>}3{/Alt}')
    expect(useUi.getState().composer).toMatchObject({ period: 'MORNING', start: '08:00', end: '12:00' })

    await user.keyboard('{Alt>}4{/Alt}')
    expect(useUi.getState().composer).toMatchObject({ period: 'AFTERNOON', start: '13:00', end: '19:00' })

    // Voltar para Horário Específico recupera a seleção original
    await user.keyboard('{Alt>}1{/Alt}')
    expect(useUi.getState().composer).toMatchObject({ period: 'CUSTOM', start: '14:00', end: '15:00' })
    expect(screen.getByLabelText('Início')).toHaveValue('14:00')
  })

  it('mudar o período trava a nova faixa e libera a anterior', async () => {
    const user = userEvent.setup()
    render(<BookingComposerHost />)
    openComposer()
    await user.keyboard('{Alt>}2{/Alt}')
    await waitFor(() =>
      expect(realtime.lockSlot).toHaveBeenCalledWith({
        room_id: 5,
        date: '2026-10-26',
        start: '08:00',
        end: '19:00',
        booking_id: null,
      }),
    )
    await waitFor(() => expect(realtime.unlock).toHaveBeenCalledWith('L1'))
  })

  it('início/fim usam passos de slot e mantêm a duração', async () => {
    const user = userEvent.setup()
    render(<BookingComposerHost />)
    openComposer()
    await user.selectOptions(screen.getByLabelText('Início'), '16:30')
    expect(screen.getByLabelText('Fim')).toHaveValue('17:30')
    const ends = Array.from((screen.getByLabelText('Fim') as HTMLSelectElement).options).map((o) => o.value)
    expect(ends[0]).toBe('17:00')
    expect(ends.at(-1)).toBe('19:00')
  })

  it('exige o solicitante sem chamar a API', async () => {
    const user = userEvent.setup()
    render(<BookingComposerHost />)
    openComposer()
    await user.keyboard('999{Enter}')
    expect(await screen.findByText('Informe o solicitante.')).toBeInTheDocument()
    expect(screen.getByLabelText(/Solicitante/)).toHaveFocus()
    expect(api.bookings.create).not.toHaveBeenCalled()
  })

  it('Esc fecha e libera a trava', async () => {
    const user = userEvent.setup()
    render(<BookingComposerHost />)
    openComposer()
    await user.keyboard('{Escape}')
    expect(useUi.getState().composer).toBeNull()
    expect(realtime.unlock).toHaveBeenCalledWith('L1')
  })

  it('RF06: sala de vídeo sem necessidade sugere salas normais livres (1 clique troca e retrava)', async () => {
    const user = userEvent.setup()
    vi.mocked(api.availability.freeRooms).mockResolvedValue([NORMAL_ROOM, NORMAL_ROOM_2])
    render(<BookingComposerHost />)
    openComposer(VIDEO_ROOM.id)

    const useRoom = await screen.findByRole('button', { name: /^Usar Sala 05/ })
    expect(api.availability.freeRooms).toHaveBeenCalledWith(
      { date: '2026-10-26', start: '14:00', end: '15:00', room_type: 'NORMAL', exclude_booking_id: undefined },
      expect.any(AbortSignal),
    )
    await user.click(useRoom)
    expect(useUi.getState().composer?.roomId).toBe(5)
    await waitFor(() =>
      expect(realtime.lockSlot).toHaveBeenCalledWith(expect.objectContaining({ room_id: 5, start: '14:00' })),
    )

    // O aviso nunca bloqueia salvar
    expect(screen.getByRole('button', { name: 'Salvar' })).toBeEnabled()
  })

  it('RF06 some quando a reunião precisa de vídeo', async () => {
    const user = userEvent.setup()
    vi.mocked(api.availability.freeRooms).mockResolvedValue([NORMAL_ROOM])
    render(<BookingComposerHost />)
    openComposer(VIDEO_ROOM.id)
    await screen.findByRole('button', { name: /^Usar Sala 05/ })
    await user.click(screen.getByRole('switch', { name: /Precisa de Videoconferência/ }))
    expect(screen.queryByRole('button', { name: /^Usar Sala 05/ })).not.toBeInTheDocument()
  })

  it('avisos do servidor viram toasts', async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookings.create).mockResolvedValue({
      ...created(),
      warnings: [{ code: 'OVER_CAPACITY', message: 'A sala comporta 4 pessoas.', data: { capacity: 4, attendees: 9 } }],
    })
    render(<BookingComposerHost />)
    openComposer()
    await user.tab()
    await user.keyboard('Ana{Enter}')
    await waitFor(() => expect(useToasts.getState().toasts.map((t) => t.title)).toContain('A sala comporta 4 pessoas.'))
    expect(useToasts.getState().toasts.find((t) => t.title === 'A sala comporta 4 pessoas.')?.kind).toBe('warning')
  })

  it('409 BOOKING_CONFLICT mostra as duas opções do fluxo 5.2', async () => {
    const user = userEvent.setup()
    const conflicting = makeBooking({
      id: 'c1',
      room_id: 5,
      start: '15:00',
      end: '16:00',
      requester_name: 'Juliana Prado',
      version: 3,
    })
    vi.mocked(api.bookings.create)
      .mockRejectedValueOnce(
        new ApiError(409, 'BOOKING_CONFLICT', 'Já existe reserva nesta sala.', {
          conflicts: [conflicting],
          alternatives: [{ id: 6, name: 'Sala 06', room_type: 'NORMAL', effective_type: 'NORMAL', capacity: 6 }],
        }),
      )
      .mockResolvedValueOnce(created({ room_id: 6, room_name: 'Sala 06' }))
    vi.mocked(api.availability.freeRooms).mockResolvedValue([NORMAL_ROOM_2])

    render(<BookingComposerHost />)
    openComposer()
    await user.keyboard('{Alt>}2{/Alt}')
    await user.tab()
    await user.keyboard('Roberto{Enter}')

    const panel = await screen.findByRole('alert', { name: 'Conflito de horário' })
    expect(panel).toHaveTextContent('Escolher outra sala livre o dia todo')
    expect(panel).toHaveTextContent('Juliana Prado')
    expect(await screen.findByRole('button', { name: /^Mover para Sala 06/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Reservar na Sala 06/ }))
    await waitFor(() => expect(api.bookings.create).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.bookings.create).mock.calls[1]![0]).toMatchObject({ room_id: 6, period_type: 'FULL_DAY' })
    await waitFor(() => expect(useUi.getState().composer).toBeNull())
  })

  it('"Mover para Sala Y" remaneja a conflitante (PATCH com version) e tenta salvar de novo', async () => {
    const user = userEvent.setup()
    const conflicting = makeBooking({ id: 'c1', room_id: 5, start: '14:30', end: '15:30', version: 3 })
    vi.mocked(api.bookings.create)
      .mockRejectedValueOnce(
        new ApiError(409, 'BOOKING_CONFLICT', 'Conflito.', { conflicts: [conflicting], alternatives: [] }),
      )
      .mockResolvedValueOnce(created())
    vi.mocked(api.bookings.update).mockResolvedValue({
      booking: { ...conflicting, room_id: 6, version: 4 },
      warnings: [],
    })
    vi.mocked(api.availability.freeRooms).mockResolvedValue([NORMAL_ROOM_2])

    render(<BookingComposerHost />)
    openComposer()
    await user.tab()
    await user.keyboard('Roberto{Enter}')
    await user.click(await screen.findByRole('button', { name: /^Mover para Sala 06/ }))

    await waitFor(() => expect(api.bookings.update).toHaveBeenCalledWith('c1', { version: 3, room_id: 6 }))
    await waitFor(() => expect(api.bookings.create).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(useUi.getState().composer).toBeNull())
  })

  it('SLOT_LOCKED mostra quem está reservando', async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookings.create).mockRejectedValue(
      new ApiError(409, 'SLOT_LOCKED', 'Beatriz Santos está reservando este horário agora.', {
        locks: [makeLock({ start: '14:30', end: '15:00' })],
      }),
    )
    render(<BookingComposerHost />)
    openComposer()
    await user.tab()
    await user.keyboard('Roberto{Enter}')
    const alert = await screen.findByText('Beatriz Santos está reservando este horário agora.')
    expect(alert.closest('[role="alert"]')).toHaveTextContent('Beatriz Santos: 14:30–15:00')
    expect(useUi.getState().composer).not.toBeNull()
  })

  it('erros de validação do servidor aparecem em pt-BR', async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookings.create).mockRejectedValue(
      new ApiError(422, 'OUTSIDE_OPERATING_HOURS', 'Fora do horário de funcionamento (08:00–19:00).'),
    )
    render(<BookingComposerHost />)
    openComposer()
    await user.tab()
    await user.keyboard('Roberto{Enter}')
    expect(await screen.findByText('Fora do horário de funcionamento (08:00–19:00).')).toBeInTheDocument()
  })

  it('trava negada ao mudar a faixa aparece no modal', async () => {
    const user = userEvent.setup()
    realtime.lockSlot.mockResolvedValue({
      ok: false,
      reason: 'DENIED',
      code: 'LOCKED_BY_OTHER',
      message: 'Carla está reservando este horário agora.',
      conflicts: [],
    })
    render(<BookingComposerHost />)
    openComposer()
    await user.keyboard('{Alt>}3{/Alt}')
    expect(await screen.findByText('Carla está reservando este horário agora.')).toBeInTheDocument()
  })
})

describe('BookingComposer (foco depois de erro)', () => {
  it('Salvar com o mouse e erro do servidor: o foco volta ao modal e Esc ainda fecha', async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookings.create).mockImplementation(async () => {
      // O navegador tira o foco do botão quando ele fica desabilitado ("salvando…").
      ;(document.activeElement as HTMLElement | null)?.blur()
      throw new ApiError(422, 'ROOM_INACTIVE', 'Esta sala está desativada e não aceita reservas.')
    })
    render(<BookingComposerHost />)
    openComposer()
    await user.type(screen.getByLabelText(/Solicitante/), 'Marcos')
    await user.click(screen.getByRole('button', { name: 'Salvar' }))

    expect(await screen.findByText('Esta sala está desativada e não aceita reservas.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Salvar' })).toHaveFocus())
    await user.keyboard('{Escape}')
    expect(useUi.getState().composer).toBeNull()
  })
})

describe('BookingComposer (edição)', () => {
  it('envia só o que mudou, com a version', async () => {
    const user = userEvent.setup()
    const booking = makeBooking({ id: 'e1', room_id: 5, room_name: 'Sala 05', version: 7, title: 'Antigo' })
    useBookings.getState().upsert(booking)
    vi.mocked(api.bookings.update).mockResolvedValue({
      booking: { ...booking, version: 8, title: 'Novo' },
      warnings: [],
    })
    render(<BookingComposerHost />)
    act(() => {
      useUi.getState().openComposer({
        mode: 'edit',
        bookingId: 'e1',
        roomId: 5,
        date: booking.date,
        period: 'CUSTOM',
        start: booking.start,
        end: booking.end,
        lockId: null,
        anchor: null,
      })
    })
    expect(screen.getByRole('dialog', { name: 'Editar reserva' })).toBeInTheDocument()
    const title = screen.getByLabelText('Assunto/Área')
    await user.clear(title)
    await user.type(title, 'Novo{Enter}')
    await waitFor(() => expect(api.bookings.update).toHaveBeenCalledWith('e1', { version: 7, title: 'Novo' }))
    // Sem mudar sala/horário não há trava nova
    expect(realtime.lockSlot).not.toHaveBeenCalled()
  })

  it('STALE_VERSION avisa, traz a versão atual e não desfaz a mudança da outra pessoa', async () => {
    const user = userEvent.setup()
    const booking = makeBooking({ id: 'e2', room_id: 5, version: 2, title: 'Pauta', requester_name: 'Marcos' })
    useBookings.getState().upsert(booking)
    vi.mocked(api.bookings.update)
      .mockRejectedValueOnce(
        new ApiError(409, 'STALE_VERSION', 'Alterada.', {
          current: { ...booking, version: 5, requester_name: 'Outra', end: '16:00' },
        }),
      )
      .mockResolvedValueOnce({ booking: { ...booking, version: 6 }, warnings: [] })
    render(<BookingComposerHost />)
    act(() => {
      useUi.getState().openComposer({
        mode: 'edit',
        bookingId: 'e2',
        roomId: 5,
        date: booking.date,
        period: 'CUSTOM',
        start: booking.start,
        end: booking.end,
        lockId: null,
        anchor: null,
      })
    })
    await user.type(screen.getByLabelText('Assunto/Área'), ' X{Enter}')
    expect(vi.mocked(api.bookings.update).mock.calls[0]![1]).toEqual({ version: 2, title: 'Pauta X' })
    expect(await screen.findByText(/Esta reserva foi alterada por outra pessoa/)).toBeInTheDocument()
    expect(useBookings.getState().data.byId.e2?.version).toBe(5)
    // O formulário mostra a versão nova nos campos não mexidos e mantém o que foi digitado aqui.
    expect(screen.getByLabelText(/Solicitante/)).toHaveValue('Outra')
    expect(screen.getByLabelText('Fim')).toHaveValue('16:00')
    expect(screen.getByLabelText('Assunto/Área')).toHaveValue('Pauta X')
    await user.click(screen.getByRole('button', { name: 'Salvar alterações' }))
    // Só a mudança desta pessoa, sobre a versão atual (sem voltar o solicitante nem o horário).
    await waitFor(() =>
      expect(vi.mocked(api.bookings.update).mock.calls[1]![1]).toEqual({ version: 5, title: 'Pauta X' }),
    )
  })
})

/** Promessa controlada pelo teste (resposta do servidor que ainda não chegou). */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

describe('BookingComposer (trava do modal)', () => {
  it('R7/R52: a trava some no servidor com o modal aberto (SLOT_UNLOCKED) → trava de novo na hora', async () => {
    render(<BookingComposerHost />)
    openComposer()
    serverEvent(unlocked('L1', 'expired'))
    await waitFor(() =>
      expect(realtime.lockSlot).toHaveBeenCalledWith({
        room_id: 5,
        date: '2026-10-26',
        start: '14:00',
        end: '15:00',
        booking_id: null,
      }),
    )
  })

  it('SLOT_UNLOCKED "saved" da própria gravação não pede trava nova', async () => {
    const user = userEvent.setup()
    const pending = deferred<BookingWriteResult>()
    vi.mocked(api.bookings.create).mockReturnValue(pending.promise)
    render(<BookingComposerHost />)
    openComposer()
    await user.tab()
    await user.keyboard('Marcos{Enter}')
    await waitFor(() => expect(api.bookings.create).toHaveBeenCalledOnce())
    serverEvent(unlocked('L1', 'saved')) // o servidor libera antes de responder o POST
    await act(async () => pending.resolve(created()))
    await waitFor(() => expect(useUi.getState().composer).toBeNull())
    await new Promise((r) => setTimeout(r, 300))
    expect(realtime.lockSlot).not.toHaveBeenCalled()
    expect(realtime.unlock).not.toHaveBeenCalled()
  })

  it('R26: trava da nova faixa que chega durante a gravação é liberada (não fica presa até o TTL)', async () => {
    const user = userEvent.setup()
    const lockReply = deferred<unknown>()
    const saved = deferred<BookingWriteResult>()
    realtime.lockSlot.mockReturnValue(lockReply.promise)
    vi.mocked(api.bookings.create).mockReturnValue(saved.promise)
    render(<BookingComposerHost />)
    openComposer()
    await user.tab()
    await user.keyboard('Marcos')
    await user.selectOptions(screen.getByLabelText('Fim'), '16:00') // nova faixa → LOCK_SLOT (debounce)
    await waitFor(() => expect(realtime.lockSlot).toHaveBeenCalledOnce())
    await user.click(screen.getByRole('button', { name: 'Salvar' })) // salva antes da resposta da trava
    await waitFor(() => expect(api.bookings.create).toHaveBeenCalledOnce())
    expect(vi.mocked(api.bookings.create).mock.calls[0]![0]).toMatchObject({ end: '16:00', lock_id: 'L1' })
    // LOCK_ACQUIRED chega antes da resposta do POST (o servidor processou o LOCK_SLOT depois de gravar).
    await act(async () =>
      lockReply.resolve({ ok: true, lock: makeLock({ lock_id: 'L2', room_id: 5, start: '14:00', end: '16:00' }) }),
    )
    await act(async () => saved.resolve(created()))
    await waitFor(() => expect(useUi.getState().composer).toBeNull())
    expect(realtime.unlock).toHaveBeenCalledWith('L2')
    expect(realtime.unlock).not.toHaveBeenCalledWith('L1') // a enviada o servidor libera ao salvar
  })

  it('R8: o modal desmontado sem fechar não deixa a trava liberada no store; ao voltar, trava de novo', async () => {
    const { rerender } = render(<BookingComposerHost />)
    openComposer()
    rerender(<></>) // a grade saiu da tela (ex.: Voltar do navegador) com o modal no store
    expect(realtime.unlock).toHaveBeenCalledWith('L1')
    expect(useUi.getState().composer?.lockId).toBeNull()
    rerender(<BookingComposerHost />)
    await waitFor(() => expect(realtime.lockSlot).toHaveBeenCalledWith(expect.objectContaining({ start: '14:00' })))
  })

  it('R52: renova a ~1/3 do lock_ttl_seconds da configuração', () => {
    vi.useFakeTimers()
    try {
      useConfig.setState({ config: { ...CONFIG, lock_ttl_seconds: 30 } })
      render(<BookingComposerHost />)
      openComposer()
      act(() => vi.advanceTimersByTime(9_900))
      expect(realtime.refreshLock).not.toHaveBeenCalled()
      act(() => vi.advanceTimersByTime(200))
      expect(realtime.refreshLock).toHaveBeenCalledWith('L1')
    } finally {
      vi.useRealTimers()
    }
  })

  it('refreshIntervalFor: 1/3 do TTL, entre 5 s e 45 s', () => {
    expect(refreshIntervalFor(120)).toBe(40_000)
    expect(refreshIntervalFor(30)).toBe(10_000)
    expect(refreshIntervalFor(6)).toBe(5_000)
    expect(refreshIntervalFor(600)).toBe(45_000)
    expect(refreshIntervalFor(undefined)).toBe(45_000)
  })

  it('R39: gravação que termina depois de outro modal abrir não fecha o modal novo', async () => {
    const user = userEvent.setup()
    const pending = deferred<BookingWriteResult>()
    vi.mocked(api.bookings.create).mockReturnValue(pending.promise)
    render(<BookingComposerHost />)
    openComposer()
    await user.tab()
    await user.keyboard('Marcos{Enter}')
    await waitFor(() => expect(api.bookings.create).toHaveBeenCalledOnce())
    openComposer(NORMAL_ROOM_2.id, 'L9') // clique em outra célula durante a gravação
    const second = useUi.getState().composer?.key
    await act(async () => pending.resolve(created()))
    expect(useUi.getState().composer?.key).toBe(second)
    expect(screen.getByRole('dialog', { name: 'Nova reserva' })).toHaveTextContent('Sala 06')
  })
})

describe('BookingComposer (teclado e clique fora)', () => {
  it('R36: clique no título deixa o foco no modal; Esc ainda fecha', async () => {
    const user = userEvent.setup()
    render(<BookingComposerHost />)
    openComposer()
    await user.click(screen.getByRole('heading', { name: 'Nova reserva' }))
    expect(screen.getByRole('dialog', { name: 'Nova reserva' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(useUi.getState().composer).toBeNull()
    expect(realtime.unlock).toHaveBeenCalledWith('L1')
  })

  it('R36: com o foco no próprio painel, Enter salva e Alt+2 troca o período', async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookings.create).mockResolvedValue(created())
    render(<BookingComposerHost />)
    openComposer()
    await user.type(screen.getByLabelText(/Solicitante/), 'Marcos')
    await user.click(screen.getByRole('heading', { name: 'Nova reserva' }))
    await user.keyboard('{Alt>}2{/Alt}')
    expect(useUi.getState().composer?.period).toBe('FULL_DAY')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(api.bookings.create).toHaveBeenCalledOnce())
  })

  it('R36: foco perdido para o <body>: Esc fecha mesmo assim', async () => {
    const user = userEvent.setup()
    render(<BookingComposerHost />)
    openComposer()
    act(() => (document.activeElement as HTMLElement | null)?.blur())
    expect(document.activeElement).toBe(document.body)
    await user.keyboard('{Escape}')
    expect(useUi.getState().composer).toBeNull()
  })

  it('R16: dentro da grade só um clique em célula/reserva fecha; cabeçalho e área vazia não', async () => {
    render(
      <>
        <div role="grid" data-testid="grid">
          <div data-testid="header">Sala 05</div>
          <div data-cell="5:3" data-testid="cell" />
        </div>
        <button type="button">Fora da grade</button>
        <BookingComposerHost />
      </>,
    )
    openComposer()
    await userEvent.setup().type(screen.getByLabelText(/Solicitante/), 'Diretoria')
    fireEvent.pointerDown(screen.getByTestId('header'))
    fireEvent.pointerDown(screen.getByTestId('grid'))
    expect(useUi.getState().composer).not.toBeNull()
    expect(screen.getByLabelText(/Solicitante/)).toHaveValue('Diretoria')
    fireEvent.pointerDown(screen.getByTestId('cell'))
    expect(useUi.getState().composer).toBeNull()

    openComposer()
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Fora da grade' }))
    expect(useUi.getState().composer).toBeNull()
  })

  it('R16: depois de rolar a grade pela barra, o foco volta ao campo e Esc/Alt+N continuam no modal', async () => {
    const user = userEvent.setup()
    render(
      <>
        <div role="grid" tabIndex={0} data-testid="grid" />
        <BookingComposerHost />
      </>,
    )
    openComposer()
    const requester = screen.getByLabelText(/Solicitante/)
    await user.type(requester, 'Diretoria')
    const grid = screen.getByTestId('grid')
    fireEvent.pointerDown(grid) // a barra de rolagem é da grade: o navegador passa o foco para ela
    act(() => grid.focus())
    fireEvent.pointerUp(grid)
    expect(requester).toHaveFocus()

    act(() => grid.focus()) // foco preso na grade (ex.: soltou fora da janela)
    await user.keyboard('{Alt>}3{/Alt}')
    expect(useUi.getState().composer?.period).toBe('MORNING')
    await user.keyboard('{Escape}')
    expect(useUi.getState().composer).toBeNull()
  })
})

describe('BookingComposer (conflito: capacidade e tempo real)', () => {
  const conflictError = (conflicts: ReturnType<typeof makeBooking>[], alternatives: unknown[] = []) =>
    new ApiError(409, 'BOOKING_CONFLICT', 'Conflito.', { conflicts, alternatives })

  beforeEach(() => {
    // respostas "once" que sobraram de outro teste não podem vazar para este
    vi.mocked(api.bookings.create).mockReset()
    vi.mocked(api.bookings.update).mockReset()
  })

  it('R12: salas que comportam a reunião conflitante vêm primeiro, com a capacidade, e o aviso do PATCH aparece', async () => {
    const user = userEvent.setup()
    const conflicting = makeBooking({
      id: 'c9',
      room_id: 5,
      start: '14:00',
      end: '15:00',
      version: 2,
      attendees: 12,
      requester_name: 'Ricardo Alves',
    })
    const small = makeRoom({ id: 7, name: 'Sala 07', capacity: 4, display_order: 1 })
    const big = makeRoom({ id: 9, name: 'Sala 09', capacity: 40, display_order: 9 })
    vi.mocked(api.availability.freeRooms).mockResolvedValue([small, big])
    vi.mocked(api.bookings.create)
      .mockRejectedValueOnce(conflictError([conflicting]))
      .mockResolvedValueOnce(created())
    vi.mocked(api.bookings.update).mockResolvedValue({
      booking: { ...conflicting, room_id: 7, version: 3 },
      warnings: [
        {
          code: 'OVER_CAPACITY',
          message: 'A sala comporta 4 pessoas e a reunião terá 12 participantes.',
          data: { capacity: 4, attendees: 12 },
        },
      ],
    })
    render(<BookingComposerHost />)
    openComposer()
    await user.tab()
    await user.keyboard('Roberto{Enter}')
    const row = (await screen.findByText('Ricardo Alves')).closest('li')!
    await waitFor(() => expect(within(row).getAllByRole('button')).toHaveLength(2))
    const buttons = within(row).getAllByRole('button')
    expect(buttons[0]).toHaveAccessibleName('Mover para Sala 09 · 40 lugares')
    expect(buttons[1]).toHaveAccessibleName('Mover para Sala 07 · 4 lugares')
    expect(row).toHaveTextContent('12 participantes')

    await user.click(buttons[1]!)
    await waitFor(() =>
      expect(useToasts.getState().toasts.map((t) => t.title)).toContain(
        'Reunião de Ricardo Alves: A sala comporta 4 pessoas e a reunião terá 12 participantes.',
      ),
    )
  })

  it('R27: reunião conflitante movida por outra pessoa aparece como resolvida; sala ocupada some da opção 1', async () => {
    const user = userEvent.setup()
    const conflicting = makeBooking({ id: 'c7', room_id: 5, start: '14:30', end: '15:30', version: 1 })
    vi.mocked(api.bookings.create).mockRejectedValueOnce(
      conflictError(
        [conflicting],
        [{ id: 6, name: 'Sala 06', room_type: 'NORMAL', effective_type: 'NORMAL', capacity: 8 }],
      ),
    )
    render(<BookingComposerHost />)
    openComposer()
    await user.tab()
    await user.keyboard('Roberto{Enter}')
    const panel = await screen.findByRole('alert', { name: 'Conflito de horário' })
    expect(within(panel).getByRole('button', { name: /^Reservar na Sala 06/ })).toBeInTheDocument()

    // Pelo tempo real: Bia ocupa a Sala 06 nesse horário e move a reunião conflitante para a Sala 01.
    act(() => {
      useBookings.getState().upsert(makeBooking({ id: 'bia', room_id: 6, start: '14:00', end: '15:00' }))
      useBookings.getState().upsert({ ...conflicting, room_id: 1, version: 2 })
    })
    expect(within(panel).queryByRole('button', { name: /^Reservar na Sala 06/ })).not.toBeInTheDocument()
    expect(panel).toHaveTextContent('Já saiu do caminho')
  })
})
