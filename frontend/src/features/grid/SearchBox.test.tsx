import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useUi } from '../../store/ui'
import { makeBooking } from '../../test/fixtures'
import { SearchBox } from './SearchBox'

vi.mock('../../api/endpoints', () => ({
  api: { bookings: { search: vi.fn() } },
}))

const { api } = await import('../../api/endpoints')

const RESULTS = [
  makeBooking({
    id: 'r1',
    ticket_number: '4521',
    requester_name: 'Marcos Lima',
    date: '2026-10-27',
    room_name: 'Sala 02',
  }),
  makeBooking({
    id: 'r2',
    ticket_number: '4522',
    requester_name: 'Mariana Souza',
    date: '2026-10-30',
    room_name: 'Sala 05',
  }),
  makeBooking({
    id: 'r3',
    ticket_number: '1999',
    requester_name: 'Mário Alves',
    date: '2026-10-01',
    room_name: 'Sala 07',
  }),
]

beforeEach(() => {
  vi.clearAllMocks()
  useUi.getState().reset()
  vi.mocked(api.bookings.search).mockResolvedValue(RESULTS)
})

describe('SearchBox', () => {
  it('pede ao menos 2 caracteres', async () => {
    const user = userEvent.setup()
    render(<SearchBox today="2026-10-26" onNavigate={vi.fn()} />)
    await user.type(screen.getByRole('combobox'), 'm')
    expect(screen.getByText('Digite ao menos 2 caracteres.')).toBeInTheDocument()
    expect(api.bookings.search).not.toHaveBeenCalled()
  })

  it('busca com debounce e lista chamado, solicitante, data e sala', async () => {
    const user = userEvent.setup()
    render(<SearchBox today="2026-10-26" onNavigate={vi.fn()} />)
    await user.type(screen.getByRole('combobox'), 'mar')
    await screen.findAllByRole('option')
    // uma única chamada para "mar", mesmo digitando letra a letra
    expect(api.bookings.search).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.bookings.search).mock.calls[0]![0]).toBe('mar')
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(3)
    expect(options[0]).toHaveTextContent('#4521')
    expect(options[0]).toHaveTextContent('Marcos Lima')
    expect(options[0]).toHaveTextContent('Ter 27/10')
    expect(options[0]).toHaveTextContent('Sala 02')
    expect(screen.getByText('Anteriores')).toBeInTheDocument()
  })

  it('setas navegam e Enter abre a reserva escolhida', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    render(<SearchBox today="2026-10-26" onNavigate={onNavigate} />)
    const input = screen.getByRole('combobox')
    await user.type(input, 'mar')
    await screen.findAllByRole('option')
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{ArrowDown}')
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true')
    expect(input).toHaveAttribute('aria-activedescendant', screen.getAllByRole('option')[1]!.id)
    await user.keyboard('{ArrowUp}{ArrowUp}')
    expect(screen.getAllByRole('option')[2]).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}')
    expect(onNavigate).toHaveBeenCalledWith(RESULTS[1])
    expect(input).toHaveValue('')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('Enter sem mexer nas setas abre o primeiro resultado', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    render(<SearchBox today="2026-10-26" onNavigate={onNavigate} />)
    await user.type(screen.getByRole('combobox'), '4521')
    await screen.findAllByRole('option')
    await user.keyboard('{Enter}')
    expect(onNavigate).toHaveBeenCalledWith(RESULTS[0])
  })

  it('Enter antes de a busca responder abre o primeiro resultado do termo digitado', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    let respond: (list: typeof RESULTS) => void = () => {}
    vi.mocked(api.bookings.search).mockImplementation(() => new Promise((resolve) => (respond = resolve)))
    render(<SearchBox today="2026-10-26" onNavigate={onNavigate} />)
    // Sem esperar o debounce: o Enter dispara a busca na hora.
    await user.type(screen.getByRole('combobox'), '4522{Enter}')
    await waitFor(() => expect(api.bookings.search).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.bookings.search).mock.calls[0]![0]).toBe('4522')
    expect(onNavigate).not.toHaveBeenCalled()
    act(() => respond([RESULTS[1]!]))
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith(RESULTS[1]))
    expect(screen.getByRole('combobox')).toHaveValue('')
  })

  it('Enter com a lista de um termo anterior na tela espera o resultado do termo atual', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    vi.mocked(api.bookings.search).mockImplementation(async (q) => (q === 'mar' ? RESULTS : [RESULTS[1]!]))
    render(<SearchBox today="2026-10-26" onNavigate={onNavigate} />)
    const input = screen.getByRole('combobox')
    await user.type(input, 'mar')
    expect(await screen.findAllByRole('option')).toHaveLength(3)
    // A lista de "mar" ainda está na tela quando a pessoa completa o termo e dá Enter.
    await user.type(input, 'iana{Enter}')
    await waitFor(() => expect(onNavigate).toHaveBeenCalledOnce())
    expect(onNavigate).toHaveBeenCalledWith(RESULTS[1])
    expect(vi.mocked(api.bookings.search).mock.calls.at(-1)![0]).toBe('mariana')
  })

  it('mostra quando nada é encontrado', async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookings.search).mockResolvedValue([])
    render(<SearchBox today="2026-10-26" onNavigate={vi.fn()} />)
    await user.type(screen.getByRole('combobox'), 'zz')
    expect(await screen.findByText(/Nenhuma reserva encontrada/)).toBeInTheDocument()
  })

  it('foca quando o atalho pede (/ ou Ctrl+K)', async () => {
    render(<SearchBox today="2026-10-26" onNavigate={vi.fn()} />)
    act(() => useUi.getState().focusSearch())
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveFocus())
  })

  it('Esc limpa a busca', async () => {
    const user = userEvent.setup()
    render(<SearchBox today="2026-10-26" onNavigate={vi.fn()} />)
    const input = screen.getByRole('combobox')
    await user.type(input, 'mar')
    await screen.findAllByRole('option')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(input).toHaveValue('')
  })
})
