import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { dayTabDates, DayTabs, stepWorkDay } from './DayTabs'

const WORK_DAYS = [0, 1, 2, 3, 4]

function renderTabs(activeDate: string, counts: Record<string, number | null> = {}) {
  const onSelect = vi.fn()
  render(
    <DayTabs activeDate={activeDate} today="2026-10-26" workDays={WORK_DAYS} counts={counts} onSelect={onSelect} />,
  )
  return { onSelect }
}

describe('DayTabs', () => {
  it('mostra as abas Seg..Sex da semana exibida em pt-BR', () => {
    renderTabs('2026-10-28')
    const tabs = within(screen.getByRole('tablist')).getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Seg 26/10', 'Ter 27/10', 'Qua 28/10', 'Qui 29/10', 'Sex 30/10'])
    expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Qua 28/10')
  })

  it('mostra a quantidade de reservas quando conhecida', () => {
    renderTabs('2026-10-26', { '2026-10-26': 4, '2026-10-27': 0, '2026-10-28': null })
    expect(screen.getByRole('tab', { name: /segunda-feira, 26 de outubro, 4 reservas, hoje/ })).toHaveTextContent(
      'Seg 26/104',
    )
    expect(screen.getByRole('tab', { name: /terça-feira, 27 de outubro, 0 reservas/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'quarta-feira, 28 de outubro' })).toHaveTextContent('Qua 28/10')
  })

  it('sábado aberto pelo calendário vira uma aba extra temporária', () => {
    renderTabs('2026-10-31')
    const labels = screen.getAllByRole('tab').map((t) => t.textContent)
    expect(labels).toEqual(['Seg 26/10', 'Ter 27/10', 'Qua 28/10', 'Qui 29/10', 'Sex 30/10', 'Sáb 31/10'])
    expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Sáb 31/10')
  })

  it('troca de dia, de semana e volta para hoje', async () => {
    const user = userEvent.setup()
    const { onSelect } = renderTabs('2026-11-04')
    await user.click(screen.getByRole('tab', { name: /sexta-feira, 6 de novembro/ }))
    expect(onSelect).toHaveBeenLastCalledWith('2026-11-06')
    await user.click(screen.getByRole('button', { name: 'Semana anterior' }))
    expect(onSelect).toHaveBeenLastCalledWith('2026-10-28')
    await user.click(screen.getByRole('button', { name: 'Próxima semana' }))
    expect(onSelect).toHaveBeenLastCalledWith('2026-11-11')
    await user.click(screen.getByRole('button', { name: 'Hoje' }))
    expect(onSelect).toHaveBeenLastCalledWith('2026-10-26')
  })

  it('setas do teclado andam entre as abas', async () => {
    const user = userEvent.setup()
    const { onSelect } = renderTabs('2026-10-30')
    screen.getByRole('tab', { selected: true }).focus()
    await user.keyboard('{ArrowRight}') // circular: depois de sexta vem segunda
    expect(onSelect).toHaveBeenLastCalledWith('2026-10-26')
    await waitFor(() => expect(screen.getByRole('tab', { name: /segunda-feira/ })).toHaveFocus())
    await user.keyboard('{ArrowLeft}')
    expect(onSelect).toHaveBeenLastCalledWith('2026-10-30')
  })

  it('"+ Calendário" abre um seletor de data', async () => {
    const user = userEvent.setup()
    const { onSelect } = renderTabs('2026-10-26')
    await user.click(screen.getByRole('button', { name: /Calendário/ }))
    const input = screen.getByLabelText('Ir para a data')
    await user.clear(input)
    await user.type(input, '2026-12-15')
    await user.click(screen.getByRole('button', { name: 'Abrir dia' }))
    expect(onSelect).toHaveBeenLastCalledWith('2026-12-15')
    expect(screen.queryByLabelText('Ir para a data')).not.toBeInTheDocument()
  })

  it('o rótulo da semana acompanha as abas', () => {
    renderTabs('2026-10-28')
    expect(screen.getByText('26 – 30 out 2026')).toBeInTheDocument()
  })
})

describe('regras das abas', () => {
  it('dayTabDates lista os dias úteis + o ativo', () => {
    expect(dayTabDates('2026-11-01', WORK_DAYS)).toEqual([
      '2026-10-26',
      '2026-10-27',
      '2026-10-28',
      '2026-10-29',
      '2026-10-30',
      '2026-11-01',
    ])
  })

  it('stepWorkDay pula o fim de semana (PageUp/PageDown)', () => {
    expect(stepWorkDay('2026-10-30', 1, WORK_DAYS)).toBe('2026-11-02')
    expect(stepWorkDay('2026-11-02', -1, WORK_DAYS)).toBe('2026-10-30')
    expect(stepWorkDay('2026-10-31', 1, WORK_DAYS)).toBe('2026-11-02')
    expect(stepWorkDay('2026-10-27', 1, WORK_DAYS)).toBe('2026-10-28')
  })
})
