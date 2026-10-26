import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RoomOut } from '../../api/types'
import { useAuth } from '../../store/auth'
import { useBookings, useDateCounts, useDayBookings } from '../../store/bookings'
import { useConfig } from '../../store/config'
import { useLocks } from '../../store/locks'
import { useRooms } from '../../store/rooms'
import { useToasts } from '../../store/toasts'
import { useUi } from '../../store/ui'
import { ADMIN, CONFIG, makeBooking, makeLock, makeRoom } from '../../test/fixtures'
import { groupByStable, HIGHLIGHT_MS, RoomGrid } from './RoomGrid'

vi.mock('../../api/endpoints', () => ({
  api: {
    bookings: { update: vi.fn(), listDay: vi.fn(async () => []), listRange: vi.fn(async () => []) },
    rooms: { list: vi.fn(async () => []) },
  },
}))
vi.mock('../../realtime/service', () => ({ getRealtime: () => null }))

const { api } = await import('../../api/endpoints')

const DATE = '2030-03-04' // segunda-feira no futuro: nada esmaecido
const TABS = ['2030-03-04', '2030-03-05', '2030-03-06']

const ROOMS: RoomOut[] = [
  makeRoom({ id: 1, name: 'Sala 01', room_type: 'VIDEO', display_order: 1, capacity: 10, resources: ['TV', 'Webcam'] }),
  makeRoom({
    id: 2,
    name: 'Sala 02',
    room_type: 'VIDEO',
    display_order: 4,
    video_status: 'MAINTENANCE',
    maintenance_ticket_id: 'INC0012345',
    maintenance_ticket_url: 'https://chamados.example.com/ticket/INC0012345',
  }),
  makeRoom({ id: 5, name: 'Sala 05', room_type: 'NORMAL', display_order: 1 }),
  makeRoom({ id: 6, name: 'Sala 06', room_type: 'NORMAL', display_order: 2 }),
  makeRoom({ id: 9, name: 'Sala Inativa', room_type: 'NORMAL', display_order: 3, is_active: false }),
]

function seed() {
  useConfig.setState({ config: CONFIG, status: 'ready', error: null })
  useRooms.setState({ byId: Object.fromEntries(ROOMS.map((r) => [r.id, r])), status: 'ready' })
  useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
  useUi.getState().reset()
  useLocks.getState().reset()
  useBookings.setState({
    data: { byId: {}, receivedSeq: {}, dates: { [DATE]: { status: 'loaded', requestSeq: 1, known: true } } },
  })
  for (const b of [
    makeBooking({
      id: 'v1',
      room_id: 1,
      date: DATE,
      start: '09:00',
      end: '10:30',
      requester_name: 'Marcos Lima',
      is_relocatable: true,
    }),
    makeBooking({
      id: 'n1',
      room_id: 5,
      date: DATE,
      start: '08:00',
      end: '19:00',
      period_type: 'FULL_DAY',
      is_full_day: true,
      requester_name: 'Treinamento',
    }),
    makeBooking({
      id: 'm1',
      room_id: 2,
      date: DATE,
      start: '14:00',
      end: '15:00',
      requires_video: true,
      video_unavailable: true,
      requester_name: 'Juliana Prado',
    }),
  ]) {
    useBookings.getState().upsert(b)
  }
  act(() => {
    useLocks.getState().upsert(
      makeLock({
        lock_id: 'k1',
        room_id: 6,
        date: DATE,
        start: '10:00',
        end: '11:00',
        user: { id: 2, full_name: 'Beatriz Santos' },
      }),
    )
    // trava minha não aparece como hachurado
    useLocks.getState().upsert(
      makeLock({
        lock_id: 'k2',
        room_id: 6,
        date: DATE,
        start: '12:00',
        end: '13:00',
        user: { id: ADMIN.id, full_name: ADMIN.full_name },
      }),
    )
  })
}

function renderGrid() {
  return render(
    <MemoryRouter>
      <RoomGrid date={DATE} />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  seed()
})

describe('RoomGrid', () => {
  it('agrupa: Videoconferência à esquerda, Normais à direita; sala em manutenção vai para as normais', () => {
    renderGrid()
    const headers = screen.getAllByRole('columnheader').map((h) => h.getAttribute('aria-label') ?? h.textContent)
    expect(headers[0]).toMatch(/Videoconferência/)
    expect(headers[1]).toMatch(/Salas Normais/)
    const rooms = headers.slice(2)
    expect(rooms.map((h) => h?.split(',')[0])).toEqual(['Sala 01', 'Sala 05', 'Sala 06', 'Sala 02'])
    expect(screen.queryByText('Sala Inativa')).not.toBeInTheDocument()

    const badge = screen.getByRole('link', { name: /Vídeo em manutenção · Chamado INC0012345/ })
    expect(badge).toHaveAttribute('href', 'https://chamados.example.com/ticket/INC0012345')
    expect(badge).toHaveAttribute('target', '_blank')
  })

  it('mostra blocos com selos e travas de outras pessoas', () => {
    renderGrid()
    expect(screen.getByRole('button', { name: /Marcos Lima.*09:00 às 10:30.*Remanejável/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Treinamento.*Dia inteiro/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Juliana Prado.*Precisa de vídeo/ })).toBeInTheDocument()
    const lock = document.querySelector('[data-lock-id="k1"]')
    expect(lock).toHaveTextContent('Reservando:')
    expect(lock).toHaveTextContent('Beatriz Santos')
    expect(document.querySelector('[data-lock-id="k2"]')).toBeNull()
  })

  it('o link do chamado abre em nova aba', () => {
    renderGrid()
    const link = within(screen.getByRole('button', { name: /Marcos Lima/ })).getByRole('link')
    expect(link).toHaveAttribute('href', 'https://chamados.example.com/ticket/123456')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it('filtros de 1 clique: Videoconferência, Normais e Livres Dia Inteiro (calculado no cliente)', () => {
    renderGrid()
    const roomHeaders = () =>
      screen
        .getAllByRole('columnheader')
        .slice(1)
        .map((h) => h.getAttribute('aria-label')?.split(',')[0])
        .filter((x) => x?.startsWith('Sala'))

    act(() => useUi.getState().setFilter('video'))
    expect(roomHeaders()).toEqual(['Sala 01'])
    act(() => useUi.getState().setFilter('normal'))
    expect(roomHeaders()).toEqual(['Sala 05', 'Sala 06', 'Sala 02'])
    act(() => useUi.getState().setFilter('free'))
    expect(roomHeaders()).toEqual(['Sala 06'])
  })

  it('filtro sem resultado mostra um estado vazio com saída', async () => {
    const user = userEvent.setup()
    act(() => useUi.getState().setFilter('free'))
    useBookings.getState().upsert(makeBooking({ id: 'n6', room_id: 6, date: DATE }))
    renderGrid()
    expect(screen.getByText('Nenhuma sala livre o dia inteiro em Seg 04/03')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Mostrar todas as salas' }))
    expect(useUi.getState().filter).toBe('all')
  })

  it('teclado: setas movem o cursor, Shift+↓ estende e Enter abre o modal da faixa', async () => {
    const user = userEvent.setup()
    renderGrid()
    await user.keyboard('{ArrowDown}') // primeira tecla posiciona o cursor (08:00 da 1ª sala)
    expect(useUi.getState().cursor).toEqual({ roomId: 1, slot: 0 })
    await user.keyboard('{ArrowRight}{ArrowRight}') // Sala 06
    expect(useUi.getState().cursor).toEqual({ roomId: 6, slot: 0 })
    await user.keyboard('{Shift>}{ArrowDown}{ArrowDown}{ArrowDown}{/Shift}')
    expect(useUi.getState().selection).toMatchObject({ roomId: 6, anchor: 0, focus: 3 })
    // 10:00 está travado pela Beatriz: a seleção não passa dele
    await user.keyboard('{Shift>}{ArrowDown}{ArrowDown}{/Shift}')
    expect(useUi.getState().selection).toMatchObject({ anchor: 0, focus: 3 })
    await user.keyboard('{Enter}')
    await waitFor(() => expect(useUi.getState().composer).not.toBeNull())
    expect(useUi.getState().composer).toMatchObject({
      roomId: 6,
      date: DATE,
      start: '08:00',
      end: '10:00',
      lockId: null,
    })
  })

  it('Enter sobre uma reserva abre o popover dela', async () => {
    const user = userEvent.setup()
    renderGrid()
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}') // Sala 01, 09:00
    expect(useUi.getState().cursor).toEqual({ roomId: 1, slot: 2 })
    await user.keyboard('{Enter}')
    expect(useUi.getState().popover?.bookingId).toBe('v1')
  })

  it('Esc limpa a seleção e depois o cursor', async () => {
    const user = userEvent.setup()
    renderGrid()
    await user.keyboard('{ArrowDown}{Shift>}{ArrowDown}{/Shift}')
    expect(useUi.getState().selection).not.toBeNull()
    await user.keyboard('{Escape}')
    expect(useUi.getState().selection).toBeNull()
    expect(useUi.getState().cursor).not.toBeNull()
    await user.keyboard('{Escape}')
    expect(useUi.getState().cursor).toBeNull()
  })

  it('clicar numa célula livre seleciona e abre o modal; numa travada avisa quem está reservando', async () => {
    renderGrid()
    const column = document.querySelector('[data-room-id="6"]') as HTMLElement
    // jsdom não tem layout: o topo da coluna é 0 e cada linha tem a altura mínima (22 px)
    fireEvent.pointerDown(column, { button: 0, pointerId: 1, clientY: 4 * 22 + 2 }) // 10:00 → travado
    expect(useUi.getState().selection).toBeNull()
    fireEvent.pointerDown(column, { button: 0, pointerId: 2, clientY: 2 * 22 + 2 }) // 09:00
    fireEvent.pointerUp(window, { pointerId: 2 })
    await waitFor(() => expect(useUi.getState().composer).toMatchObject({ roomId: 6, start: '09:00', end: '09:30' }))
  })

  it('destaque da busca some sozinho, mesmo chegando eventos do tempo real no meio', () => {
    vi.useFakeTimers()
    try {
      renderGrid()
      act(() => useUi.getState().highlight('v1'))
      expect(document.querySelector('[data-booking-id="v1"]')).toHaveClass('animate-highlight')
      // Reserva de outro dia chegando pelo tempo real: a grade re-renderiza antes do fim do destaque.
      act(() => vi.advanceTimersByTime(HIGHLIGHT_MS / 2))
      act(() => useBookings.getState().upsert(makeBooking({ id: 'x1', room_id: 6, date: '2030-03-05' })))
      act(() => vi.advanceTimersByTime(HIGHLIGHT_MS / 2 + 10))
      expect(useUi.getState().highlightId).toBeNull()
      expect(document.querySelector('[data-booking-id="v1"]')).not.toHaveClass('animate-highlight')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('RoomGrid (sala desativada — D1)', () => {
  const legacy = () =>
    makeBooking({ id: 'old', room_id: 9, date: DATE, start: '10:00', end: '11:00', requester_name: 'Helena Inativa' })

  it('a coluna da sala desativada aparece só nos dias em que ela ainda tem reservas, com o rótulo', () => {
    useBookings.getState().upsert(legacy())
    renderGrid()
    const header = screen.getByRole('columnheader', { name: /^Sala Inativa/ })
    expect(header).toHaveAccessibleName('Sala Inativa, 8 lugares, sala desativada')
    expect(within(header).getByText('Sala desativada')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Helena Inativa/ })).toBeInTheDocument()
    // grade e contagem do dia batem: 4 reservas ativas, 4 blocos
    expect(document.querySelectorAll('[data-booking-id]')).toHaveLength(4)
  })

  it('não aceita reserva nova: clique numa célula livre avisa e não seleciona', () => {
    useBookings.getState().upsert(legacy())
    renderGrid()
    const column = document.querySelector('[data-room-id="9"]') as HTMLElement
    fireEvent.pointerDown(column, { button: 0, pointerId: 3, clientY: 2 * 22 + 2 }) // 09:00, livre
    fireEvent.pointerUp(window, { pointerId: 3 })
    expect(useUi.getState().selection).toBeNull()
    expect(useUi.getState().composer).toBeNull()
    expect(useToasts.getState().toasts.at(-1)?.title).toBe('Sala desativada: não aceita reservas novas.')
  })

  it('sem reservas no dia, a sala desativada não aparece', () => {
    renderGrid()
    expect(screen.queryByRole('columnheader', { name: /^Sala Inativa/ })).not.toBeInTheDocument()
  })
})

describe('RoomGrid (estados de carga)', () => {
  it('R15: a lista de salas falhou → erro com "Tentar de novo" (não um esqueleto eterno)', async () => {
    const user = userEvent.setup()
    useRooms.setState({ byId: {}, status: 'error', error: 'Erro inesperado no servidor (500).' })
    vi.mocked(api.rooms.list).mockResolvedValueOnce(ROOMS)
    renderGrid()
    expect(screen.getByText('Não foi possível carregar as salas')).toBeInTheDocument()
    expect(screen.getByText(/Erro inesperado no servidor \(500\)/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Tentar de novo' }))
    await screen.findByRole('grid')
  })

  it('R15: uma sala avulsa (evento) sem a lista carregada não vira uma grade incompleta', () => {
    useRooms.setState({ byId: { 1: ROOMS[0]! }, status: 'loading', error: null })
    renderGrid()
    expect(screen.queryByRole('grid')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Carregando salas')).toBeInTheDocument()
  })

  it('R41: "Livres Dia Inteiro" espera as reservas do dia (não lista todas as salas como livres)', () => {
    useBookings.setState({ data: { byId: {}, receivedSeq: {}, dates: {} } })
    act(() => useUi.getState().setFilter('free'))
    renderGrid()
    expect(screen.queryByRole('grid')).not.toBeInTheDocument()
    expect(screen.getByText('Procurando salas livres o dia inteiro…')).toBeInTheDocument()

    act(() =>
      useBookings.setState({
        data: { byId: {}, receivedSeq: {}, dates: { [DATE]: { status: 'error', requestSeq: 1, known: false } } },
      }),
    )
    expect(screen.getByText('Não foi possível carregar as reservas de Seg 04/03')).toBeInTheDocument()
  })
})

describe('RoomGrid (acessibilidade e desempenho)', () => {
  it('R42: estrutura de grade válida (grid → row → columnheader/gridcell) e o cursor é anunciado', async () => {
    const user = userEvent.setup()
    renderGrid()
    const grid = screen.getByRole('grid')
    const rows = within(grid).getAllByRole('row')
    expect(rows).toHaveLength(3)
    for (const header of within(grid).getAllByRole('columnheader'))
      expect(header.closest('[role="row"]')).not.toBeNull()
    for (const cell of within(grid).getAllByRole('gridcell')) expect(cell.closest('[role="row"]')).toBe(rows[2])
    expect(grid).not.toHaveAttribute('aria-rowcount')

    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}') // Sala 01, 09:00 (reserva do Marcos)
    const announcer = document.querySelector('[data-grid-announcer]')!
    expect(announcer).toHaveAttribute('aria-live', 'polite')
    expect(announcer).toHaveTextContent(
      'Sala 01, 09:00 às 09:30: ocupada por Marcos Lima, chamado 123456, 09:00 às 10:30',
    )
    await user.keyboard('{ArrowRight}{ArrowRight}{ArrowDown}{ArrowDown}') // Sala 06, 10:00 (travada pela Beatriz)
    expect(announcer).toHaveTextContent('Sala 06, 10:00 às 10:30: Beatriz Santos está reservando')
    await user.keyboard('{ArrowLeft}') // Sala 05 tem o dia inteiro ocupado; Sala 01→05→06: volta para a 05
    expect(announcer).toHaveTextContent(/^Sala 05, 10:00 às 10:30: ocupada por Treinamento/)
  })

  it('R33: o selo de manutenção mantém o nº do chamado visível (o texto longo encolhe primeiro)', () => {
    renderGrid()
    const badge = screen.getByRole('link', { name: 'Vídeo em manutenção · Chamado INC0012345' })
    expect(within(badge).getByText('· #INC0012345')).toHaveClass('shrink-0')
  })

  it('R35: evento de outra semana não re-renderiza as colunas (grupos e blocos estáveis)', () => {
    const grouped = groupByStable([{ room_id: 1 }, { room_id: 2 }], (x) => x.room_id, new Map())
    const a = grouped.get(1)!
    const again = groupByStable([a[0]!, grouped.get(2)![0]!], (x) => x.room_id, grouped)
    expect(again.get(1)).toBe(a)

    const day = renderHook(() => useDayBookings(DATE))
    const counts = renderHook(() => useDateCounts(TABS))
    const dayBefore = day.result.current
    const countsBefore = counts.result.current
    act(() => useBookings.getState().upsert(makeBooking({ id: 'far', room_id: 1, date: '2030-04-15' })))
    expect(day.result.current).toBe(dayBefore)
    expect(counts.result.current).toBe(countsBefore)
    act(() => useBookings.getState().upsert(makeBooking({ id: 'near', room_id: 6, date: DATE })))
    expect(day.result.current).not.toBe(dayBefore)
    expect(day.result.current).toHaveLength(4)
  })
})
