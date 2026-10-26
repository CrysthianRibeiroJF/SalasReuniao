import { create } from 'zustand'

import { errorMessage } from '../api/errors'
import { api } from '../api/endpoints'
import type { ConfigOut } from '../api/types'

interface ConfigState {
  config: ConfigOut | null
  status: 'idle' | 'loading' | 'ready' | 'error'
  error: string | null
  load: () => Promise<void>
  reset: () => void
}

export const useConfig = create<ConfigState>()((set, get) => ({
  config: null,
  status: 'idle',
  error: null,
  load: async () => {
    if (get().status === 'loading') return
    // O erro anterior fica visível durante a nova tentativa (sem piscar a tela).
    set({ status: 'loading' })
    try {
      const config = await api.config.get()
      set({ config, status: 'ready', error: null })
    } catch (error) {
      set({ status: 'error', error: errorMessage(error, 'Não foi possível carregar a configuração.') })
    }
  },
  reset: () => set({ config: null, status: 'idle', error: null }),
}))

/** Configuração já carregada (o AppShell só renderiza as páginas depois do load). */
export function useLoadedConfig(): ConfigOut {
  const config = useConfig((s) => s.config)
  if (!config) throw new Error('Configuração ainda não carregada')
  return config
}
