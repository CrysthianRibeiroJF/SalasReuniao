import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/errors'
import type { AuditEntryOut, ImportReport } from '../../api/types'
import { RequireAdmin } from '../auth/guards'
import { useAuth } from '../../store/auth'
import { useConfig } from '../../store/config'
import { useRooms } from '../../store/rooms'
import { ADMIN, CONFIG, makeBooking, makeRoom } from '../../test/fixtures'
import { AdminIndex, AdminLayout } from './AdminLayout'
import { AuditPage } from './AuditPage'
import { ImportPage, ReportView } from './ImportPage'
import { RoomDialog } from './RoomDialog'

vi.mock('../../api/endpoints', () => ({
  api: {
    excel: { importXlsx: vi.fn() },
    audit: { list: vi.fn(async () => []) },
    users: { list: vi.fn(async () => []) },
    bookings: { listDay: vi.fn(async () => []), listRange: vi.fn(async () => []) },
    rooms: { update: vi.fn(), create: vi.fn(), setMaintenance: vi.fn(), clearMaintenance: vi.fn() },
  },
}))

const { api } = await import('../../api/endpoints')

const RECEPTIONIST = { ...ADMIN, id: 2, username: 'bia', full_name: 'Beatriz Santos', role: 'RECEPTIONIST' as const }

function renderAdmin(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<AdminIndex />} />
          <Route
            path="salas"
            element={
              <RequireAdmin>
                <p>página de salas</p>
              </RequireAdmin>
            }
          />
          <Route
            path="usuarios"
            element={
              <RequireAdmin>
                <p>página de usuários</p>
              </RequireAdmin>
            }
          />
          <Route
            path="importar"
            element={
              <RequireAdmin>
                <ImportPage />
              </RequireAdmin>
            }
          />
          <Route path="auditoria" element={<AuditPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  useConfig.setState({ config: CONFIG, status: 'ready', error: null })
})

describe('administração', () => {
  it('admin vê todas as seções', () => {
    useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
    renderAdmin('/admin')
    const nav = screen.getByRole('navigation', { name: 'Seções da administração' })
    expect(
      within(nav)
        .getAllByRole('link')
        .map((l) => l.textContent),
    ).toEqual(['Salas', 'Usuários', 'Importar planilha', 'Auditoria'])
    expect(screen.getByText('página de salas')).toBeInTheDocument()
  })

  it('recepcionista não vê Salas/Usuários/Importar e é levada à Auditoria', async () => {
    useAuth.setState({ user: RECEPTIONIST, token: 't', status: 'authenticated' })
    renderAdmin('/admin/usuarios')
    expect(screen.queryByRole('navigation', { name: 'Seções da administração' })).not.toBeInTheDocument()
    expect(screen.queryByText('página de usuários')).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Auditoria' })).toBeInTheDocument()
    // recepção não lista usuários (rota só de admin)
    expect(api.users.list).not.toHaveBeenCalled()
  })

  it('importação: simula (dry-run), confirma e mostra o relatório final', async () => {
    const user = userEvent.setup()
    useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
    const sheet = {
      sheet_name: 'Seg 26-10',
      date: '2026-10-26',
      status: 'DRY_RUN' as const,
      bookings_found: 3,
      bookings_imported: 0,
      bookings_skipped: 1,
      warnings: ['Linha 9: conflito com reserva existente — pulada.'],
    }
    vi.mocked(api.excel.importXlsx)
      .mockResolvedValueOnce({
        dry_run: true,
        sheets: [sheet],
        total_found: 3,
        total_imported: 0,
        total_skipped: 1,
        unknown_rooms: ['Sala Azul'],
        errors: [],
      })
      .mockResolvedValueOnce({
        dry_run: false,
        sheets: [{ ...sheet, status: 'IMPORTED', bookings_imported: 2 }],
        total_found: 3,
        total_imported: 2,
        total_skipped: 1,
        unknown_rooms: [],
        errors: [],
      })
    renderAdmin('/admin/importar')

    const file = new File(['xlsx'], 'agenda.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    await user.upload(document.getElementById('import-file') as HTMLInputElement, file)
    expect(screen.getByText('agenda.xlsx')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Simular importação' }))

    expect(await screen.findByText('Simulação — nada foi gravado')).toBeInTheDocument()
    expect(api.excel.importXlsx).toHaveBeenLastCalledWith(file, { dryRun: true, includePast: false })
    expect(screen.getByText('Sala Azul')).toBeInTheDocument()
    expect(screen.getByText('Simulada')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '1 aviso' }))
    expect(screen.getByText('Linha 9: conflito com reserva existente — pulada.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Importar de verdade' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Importar agora' }))
    await waitFor(() =>
      expect(api.excel.importXlsx).toHaveBeenLastCalledWith(file, { dryRun: false, includePast: false }),
    )
    expect(await screen.findByText('Importação concluída')).toBeInTheDocument()
    expect(screen.getByText('Importada')).toBeInTheDocument()
  })

  it('importação recusa arquivos que não são .xlsx', async () => {
    const user = userEvent.setup({ applyAccept: false })
    useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
    renderAdmin('/admin/importar')
    await user.upload(document.getElementById('import-file') as HTMLInputElement, new File(['x'], 'agenda.xls'))
    expect(screen.getByRole('alert')).toHaveTextContent('Escolha um arquivo .xlsx')
    expect(screen.getByRole('button', { name: 'Simular importação' })).toBeDisabled()
  })

  it('importação recusa arquivo acima do limite do servidor (10 MB) antes de enviar', async () => {
    const user = userEvent.setup()
    useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
    renderAdmin('/admin/importar')
    const input = document.getElementById('import-file') as HTMLInputElement
    const big = new File([new Uint8Array(Math.round(10.5 * 1024 * 1024))], 'agenda.xlsx')
    await user.upload(input, big)
    expect(screen.getByRole('alert')).toHaveTextContent('O arquivo tem 10,5 MB; o limite é 10 MB.')
    expect(screen.getByRole('button', { name: 'Simular importação' })).toBeDisabled()
    expect(api.excel.importXlsx).not.toHaveBeenCalled()

    // Exatamente no limite ainda é aceito.
    await user.upload(input, new File([new Uint8Array(10 * 1024 * 1024)], 'agenda.xlsx'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('· 10,0 MB')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Simular importação' })).toBeEnabled()
  })

  it('auditoria filtra por ação e mostra o diff legível', async () => {
    const user = userEvent.setup()
    useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
    vi.mocked(api.audit.list).mockResolvedValue([
      {
        id: 1,
        booking_id: 'b1',
        user: { id: 2, full_name: 'Beatriz Santos' },
        action: 'MOVED_ROOM',
        previous_state: { room_name: 'Sala 01', start: '14:00', end: '15:00' },
        new_state: { room_name: 'Sala 03', start: '14:00', end: '15:00' },
        created_at: '2026-10-26T14:03:00-03:00',
        booking_ticket_number: '4521',
        booking_requester_name: 'Marcos Lima',
        booking_room_name: 'Sala 03',
        booking_date: '2026-10-26',
      },
    ])
    renderAdmin('/admin/auditoria')
    expect(await screen.findByText('Trocou de sala', { selector: 'span span' })).toBeInTheDocument()
    expect(screen.getByText('Sala 01 → Sala 03')).toBeInTheDocument()
    expect(screen.getByText('26/10/2026 14:03')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Detalhes' }))
    expect(screen.getByRole('columnheader', { name: 'Antes' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Ação'), 'CANCELLED')
    await waitFor(() =>
      expect(api.audit.list).toHaveBeenLastCalledWith({
        action: 'CANCELLED',
        limit: 50,
        offset: 0,
        date: undefined,
        booking_date: undefined,
        user_id: undefined,
      }),
    )
    await user.selectOptions(screen.getByLabelText('Usuário'), '2')
    await waitFor(() => expect(api.audit.list).toHaveBeenLastCalledWith(expect.objectContaining({ user_id: 2 })))
    await user.type(screen.getByLabelText('Dia da reserva'), '2026-10-26')
    await waitFor(() =>
      expect(api.audit.list).toHaveBeenLastCalledWith(expect.objectContaining({ booking_date: '2026-10-26' })),
    )
  })
})

describe('administração (correções)', () => {
  const dryRun = (overrides: Partial<ImportReport> = {}): ImportReport => ({
    dry_run: true,
    sheets: [
      {
        sheet_name: 'Seg 26-10',
        date: '2026-10-26',
        status: 'DRY_RUN',
        bookings_found: 2,
        bookings_imported: 0,
        bookings_skipped: 0,
        warnings: [],
      },
    ],
    total_found: 2,
    total_imported: 0,
    total_skipped: 0,
    unknown_rooms: [],
    errors: [],
    ...overrides,
  })

  it('R34/R45: mudar "Incluir datas passadas" depois de simular descarta a simulação; a real usa as opções simuladas', async () => {
    const user = userEvent.setup()
    useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
    vi.mocked(api.excel.importXlsx)
      .mockResolvedValueOnce(dryRun())
      .mockResolvedValueOnce(dryRun({ total_found: 5 }))
      .mockResolvedValueOnce({ ...dryRun(), dry_run: false, total_imported: 5 })
    renderAdmin('/admin/importar')
    const file = new File(['xlsx'], 'agenda.xlsx')
    await user.upload(document.getElementById('import-file') as HTMLInputElement, file)
    await user.click(screen.getByRole('button', { name: 'Simular importação' }))
    expect(await screen.findByRole('button', { name: 'Importar de verdade' })).toBeInTheDocument()

    await user.click(screen.getByRole('switch', { name: /Incluir datas passadas/ }))
    expect(screen.queryByRole('button', { name: 'Importar de verdade' })).not.toBeInTheDocument()
    expect(screen.queryByText('Simulação — nada foi gravado')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Simular importação' }))
    expect(api.excel.importXlsx).toHaveBeenLastCalledWith(file, { dryRun: true, includePast: true })
    await user.click(await screen.findByRole('button', { name: 'Importar de verdade' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Importar agora' }))
    await waitFor(() =>
      expect(api.excel.importXlsx).toHaveBeenLastCalledWith(file, { dryRun: false, includePast: true }),
    )
  })

  it('R37: "Carregar mais" com registros novos no meio não repete linhas', async () => {
    const user = userEvent.setup()
    useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
    const entry = (id: number): AuditEntryOut => ({
      id,
      booking_id: `b${id}`,
      user: { id: 2, full_name: 'Beatriz Santos' },
      action: 'CREATED',
      previous_state: null,
      new_state: { room_name: 'Sala 01', date: '2026-10-26', start: '14:00', end: '15:00' },
      created_at: '2026-10-26T14:03:00-03:00',
      booking_ticket_number: null,
      booking_requester_name: `Reserva ${id}`,
      booking_room_name: 'Sala 01',
      booking_date: '2026-10-26',
    })
    const page = (from: number, to: number) => Array.from({ length: from - to + 1 }, (_, i) => entry(from - i))
    // 1ª página: 100..51; chegaram 4 registros novos (101..104), então a 2ª página começa repetindo 54..51.
    vi.mocked(api.audit.list).mockResolvedValueOnce(page(100, 51)).mockResolvedValueOnce(page(54, 5))
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    renderAdmin('/admin/auditoria')
    expect(await screen.findByText('Reserva 100')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Carregar mais' }))
    expect(await screen.findByText('Reserva 5')).toBeInTheDocument()
    expect(screen.getAllByText('Reserva 54')).toHaveLength(1)
    expect(screen.getAllByRole('row')).toHaveLength(1 + 96)
    expect(errors).not.toHaveBeenCalledWith(expect.stringContaining('same key'), expect.anything(), expect.anything())
    expect(api.audit.list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }))
  })

  it('D1: desativar sala com reservas futuras mostra a lista com link para cada uma na grade', async () => {
    const user = userEvent.setup()
    useAuth.setState({ user: ADMIN, token: 't', status: 'authenticated' })
    const room = makeRoom({ id: 11, name: 'Sala 11 - Temporária' })
    useRooms.setState({ byId: { 11: room }, status: 'ready', error: null })
    const future = makeBooking({
      id: 'f1',
      room_id: 11,
      date: '2026-10-29',
      start: '10:00',
      end: '11:00',
      requester_name: 'Helena Inativa',
      ticket_number: '777001',
    })
    vi.mocked(api.rooms.update).mockRejectedValueOnce(
      new ApiError(409, 'ROOM_HAS_FUTURE_BOOKINGS', 'A sala tem 3 reservas que ainda não aconteceram.', {
        count: 3,
        bookings: [future],
      }),
    )
    render(
      <MemoryRouter>
        <RoomDialog open room={room} onClose={() => {}} />
      </MemoryRouter>,
    )
    const dialog = screen.getByRole('dialog', { name: 'Editar Sala 11 - Temporária' })
    await user.click(within(dialog).getByRole('switch', { name: /Sala ativa/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Salvar alterações' }))

    const notice = await within(dialog).findByRole('alert', { name: 'Reservas que impedem a desativação' })
    expect(api.rooms.update).toHaveBeenCalledWith(11, { is_active: false })
    expect(notice).toHaveTextContent('A sala tem 3 reservas que ainda não aconteceram.')
    expect(notice).toHaveTextContent('Qui 29/10 · 10:00–11:00')
    expect(notice).toHaveTextContent('Helena Inativa')
    expect(notice).toHaveTextContent('#777001')
    expect(notice).toHaveTextContent('Mostrando 1 de 3 reservas')
    const link = within(notice).getByRole('link', { name: /Abrir na grade/ })
    expect(link).toHaveAttribute('href', '/?date=2026-10-29&highlight=f1')
    expect(link).toHaveAttribute('target', '_blank')
    // nada foi salvo: a sala continua ativa no estado local
    expect(useRooms.getState().byId[11]?.is_active).toBe(true)
  })

  it('aba ignorada por outro motivo (oculta) não diz "data não reconhecida": a situação aponta para o aviso', async () => {
    const user = userEvent.setup()
    const hidden =
      'Aba oculta na planilha: ignorada. Se as reservas dela valem, reexiba a aba no Excel e importe de novo.'
    render(
      <ReportView
        report={dryRun({
          sheets: [
            {
              sheet_name: 'Seg 26-10 (antiga)',
              date: null,
              status: 'SKIPPED_UNRECOGNIZED',
              bookings_found: 0,
              bookings_imported: 0,
              bookings_skipped: 0,
              warnings: [hidden],
            },
          ],
          total_found: 0,
        })}
      />,
    )
    const row = screen.getByRole('row', { name: /Seg 26-10 \(antiga\)/ })
    expect(row).toHaveTextContent('Ignorada (veja o aviso)')
    expect(row).not.toHaveTextContent('data não reconhecida')
    await user.click(within(row).getByRole('button', { name: '1 aviso' }))
    expect(screen.getByText(hidden)).toBeInTheDocument()
  })
})
