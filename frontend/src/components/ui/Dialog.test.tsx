import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Dialog } from './Dialog'

function renderDialog(props: { busy?: boolean } = {}) {
  const onClose = vi.fn()
  render(
    <Dialog open title="Vídeo em manutenção" onClose={onClose} {...props}>
      <input aria-label="Chamado" />
      <button type="button">Salvar</button>
    </Dialog>,
  )
  return onClose
}

describe('Dialog', () => {
  it('foca o primeiro campo e fecha com Esc', async () => {
    const user = userEvent.setup()
    const onClose = renderDialog()
    expect(screen.getByLabelText('Chamado')).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('Esc ainda fecha quando o foco caiu no <body> (botão desabilitado ao salvar)', () => {
    const onClose = renderDialog()
    ;(document.activeElement as HTMLElement).blur()
    expect(document.activeElement).toBe(document.body)
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('ocupado (busy) não fecha com Esc', () => {
    const onClose = renderDialog({ busy: true })
    fireEvent.keyDown(screen.getByLabelText('Chamado'), { key: 'Escape' })
    ;(document.activeElement as HTMLElement).blur()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })
})
