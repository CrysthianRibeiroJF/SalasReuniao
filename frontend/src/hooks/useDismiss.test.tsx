import { fireEvent, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { isOnScrollbar, useDismiss } from './useDismiss'

/** Elemento rolável de 200×100 (área de conteúdo) com barra vertical de 12 px. */
function scroller(): HTMLDivElement {
  const el = document.createElement('div')
  Object.defineProperties(el, {
    clientWidth: { value: 200 },
    clientHeight: { value: 100 },
    scrollWidth: { value: 200 },
    scrollHeight: { value: 900 },
    clientLeft: { value: 0 },
    clientTop: { value: 0 },
  })
  el.getBoundingClientRect = () => ({ left: 10, top: 20, right: 222, bottom: 120, width: 212, height: 100 }) as DOMRect
  return el
}

function Panel({ onDismiss }: { onDismiss: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useDismiss(ref, onDismiss)
  return <div ref={ref}>painel</div>
}

describe('useDismiss', () => {
  it('isOnScrollbar: só a faixa da barra de rolagem conta', () => {
    const el = scroller()
    expect(isOnScrollbar({ clientX: 215, clientY: 50 }, el)).toBe(true) // x 205 ≥ clientWidth 200
    expect(isOnScrollbar({ clientX: 100, clientY: 50 }, el)).toBe(false)
    const plain = document.createElement('div')
    expect(isOnScrollbar({ clientX: 215, clientY: 50 }, plain)).toBe(false)
  })

  it('R16: arrastar a barra de rolagem de outro elemento não fecha o painel; um clique de verdade fecha', () => {
    const onDismiss = vi.fn()
    render(<Panel onDismiss={onDismiss} />)
    const el = scroller()
    document.body.appendChild(el)
    fireEvent.pointerDown(el, { clientX: 215, clientY: 50 })
    expect(onDismiss).not.toHaveBeenCalled()
    fireEvent.pointerDown(el, { clientX: 100, clientY: 50 })
    expect(onDismiss).toHaveBeenCalledOnce()
    fireEvent.pointerDown(screen.getByText('painel'))
    expect(onDismiss).toHaveBeenCalledOnce()
    el.remove()
  })
})
