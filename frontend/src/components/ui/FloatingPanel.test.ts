import { describe, expect, it } from 'vitest'

import { placePanel } from './FloatingPanel'

const viewport = { width: 1366, height: 657 }
const panel = { width: 432, height: 480 }

describe('placePanel', () => {
  it('abre à direita da seleção quando cabe', () => {
    expect(placePanel({ left: 300, top: 200, width: 130, height: 44 }, panel, viewport)).toEqual({
      left: 438,
      top: 167,
    })
  })

  it('vai para a esquerda perto da borda direita', () => {
    expect(placePanel({ left: 1230, top: 120, width: 130, height: 44 }, panel, viewport)).toEqual({
      left: 790,
      top: 120,
    })
  })

  it('nunca sai da janela (topo e base)', () => {
    const box = placePanel({ left: 300, top: 600, width: 130, height: 44 }, panel, viewport)
    expect(box.top + panel.height).toBeLessThanOrEqual(viewport.height - 10)
    expect(placePanel({ left: 300, top: -50, width: 130, height: 44 }, panel, viewport).top).toBe(10)
  })

  it('sem espaço dos lados, centraliza abaixo da âncora', () => {
    const narrow = { width: 500, height: 900 }
    expect(placePanel({ left: 200, top: 100, width: 100, height: 40 }, { width: 432, height: 300 }, narrow)).toEqual({
      left: 34,
      top: 148,
    })
  })
})
