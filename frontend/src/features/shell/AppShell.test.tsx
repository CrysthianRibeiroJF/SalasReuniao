import { act, render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useConfig } from '../../store/config'
import { useRooms } from '../../store/rooms'
import { CONFIG } from '../../test/fixtures'
import { AppShell } from './AppShell'

vi.mock('../../api/endpoints', () => ({
  api: {
    config: { get: vi.fn(async () => CONFIG) },
    rooms: { list: vi.fn() },
  },
}))
vi.mock('../../realtime/service', () => ({ startRealtime: vi.fn(), stopRealtime: vi.fn() }))

const { api } = await import('../../api/endpoints')

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<p>grade</p>} />
          <Route path="/admin/salas" element={<p>salas</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  useConfig.getState().reset()
  useRooms.getState().reset()
})

describe('AppShell', () => {
  it('K8: na administração os avisos ficam à esquerda (longe das ações das tabelas); na grade, à direita', async () => {
    vi.mocked(api.rooms.list).mockResolvedValue([])
    const admin = renderAt('/admin/salas')
    expect(document.querySelector('[aria-live="polite"]')).toHaveAttribute('data-placement', 'bottom-left')
    admin.unmount()
    renderAt('/')
    expect(document.querySelector('[aria-live="polite"]')).toHaveAttribute('data-placement', 'bottom-right')
  })

  it('R15: só a lista de salas falhou → tenta de novo sozinha', async () => {
    vi.useFakeTimers()
    try {
      vi.mocked(api.rooms.list).mockRejectedValueOnce(new Error('500')).mockResolvedValueOnce([])
      renderAt('/')
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10)
      })
      expect(useRooms.getState().status).toBe('error')
      expect(useConfig.getState().status).toBe('ready')
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000)
      })
      expect(api.rooms.list).toHaveBeenCalledTimes(2)
      expect(useRooms.getState().status).toBe('ready')
    } finally {
      vi.useRealTimers()
    }
  })
})
