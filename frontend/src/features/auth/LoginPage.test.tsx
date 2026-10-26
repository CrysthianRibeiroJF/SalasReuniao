import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/errors'
import { useAuth } from '../../store/auth'
import { ADMIN } from '../../test/fixtures'
import { RequireAuth } from './guards'
import { formatCountdown, LoginPage, loginDestination } from './LoginPage'

vi.mock('../../api/endpoints', () => ({ api: { auth: { login: vi.fn(), me: vi.fn() } } }))

const { api } = await import('../../api/endpoints')

const BIA = { ...ADMIN, id: 2, username: 'bia', full_name: 'Bia Recepção', role: 'RECEPTIONIST' as const }

function Where() {
  const location = useLocation()
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>
}

function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="*"
          element={
            <RequireAuth>
              <Where />
            </RequireAuth>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

function session(user = ADMIN) {
  return {
    access_token: `tok-${user.username}`,
    token_type: 'bearer' as const,
    expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    user,
  }
}

async function signIn(username: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Usuário'), username)
  await user.type(screen.getByLabelText('Senha', { selector: 'input' }), 'segredo')
  await user.click(screen.getByRole('button', { name: 'Entrar' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  useAuth.setState({
    token: null,
    user: null,
    expiresAt: null,
    status: 'anonymous',
    logoutReason: null,
    previousUsername: null,
  })
})

describe('LoginPage', () => {
  it.each([
    ['expired', 'Sua sessão expirou. Entre novamente.'],
    ['revoked', 'Sua sessão foi encerrada porque a senha foi alterada. Entre novamente.'],
    ['disabled', 'Seu acesso foi desativado. Fale com o administrador.'],
  ] as const)('D2: aviso para sessão "%s"', (reason, message) => {
    useAuth.setState({ logoutReason: reason })
    renderApp('/login')
    expect(screen.getByRole('status')).toHaveTextContent(message)
  })

  it('"Sair" (manual) não mostra aviso', () => {
    useAuth.setState({ logoutReason: 'manual' })
    renderApp('/login')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('D4: 429 mostra a mensagem com o tempo restante e bloqueia o botão até acabar', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      vi.mocked(api.auth.login).mockRejectedValueOnce(
        new ApiError(429, 'TOO_MANY_ATTEMPTS', 'Muitas tentativas de entrada. Aguarde.', { retry_after_seconds: 65 }),
      )
      renderApp('/login')
      await signIn('ana')
      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('Muitas tentativas de entrada. Aguarde.')
      expect(alert).toHaveTextContent('Tente novamente em 1:05.')
      expect(screen.getByRole('button', { name: 'Entrar' })).toBeDisabled()

      act(() => vi.advanceTimersByTime(5_000))
      expect(screen.getByRole('alert')).toHaveTextContent('Tente novamente em 1:00.')
      act(() => vi.advanceTimersByTime(61_000))
      await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
      expect(screen.getByRole('button', { name: 'Entrar' })).toBeEnabled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('formatCountdown', () => {
    expect(formatCountdown(65)).toBe('1:05')
    expect(formatCountdown(900)).toBe('15:00')
    expect(formatCountdown(0.2)).toBe('0:01')
  })
})

describe('para onde ir depois de entrar (R40)', () => {
  it('loginDestination: só volta à página anterior se for a mesma pessoa (ou link direto)', () => {
    expect(loginDestination('/admin/usuarios', 'admin', 'admin')).toBe('/admin/usuarios')
    expect(loginDestination('/admin/usuarios', 'admin', 'bia')).toBe('/')
    expect(loginDestination('/?date=2026-11-10', null, 'bia')).toBe('/?date=2026-11-10')
    expect(loginDestination(undefined, null, 'bia')).toBe('/')
  })

  it('admin sai na página de usuários; a recepcionista que entra em seguida cai na grade', async () => {
    useAuth.setState({ token: 'tok-admin', user: ADMIN, status: 'authenticated' })
    renderApp('/admin/usuarios')
    expect(screen.getByTestId('where')).toHaveTextContent('/admin/usuarios')
    act(() => useAuth.getState().logout('manual'))
    await screen.findByRole('button', { name: 'Entrar' })

    vi.mocked(api.auth.login).mockResolvedValueOnce(session(BIA))
    await signIn('bia')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/$/))
  })

  it('sessão vencida: a mesma pessoa volta ao mesmo dia', async () => {
    useAuth.setState({ token: 'tok-admin', user: ADMIN, status: 'authenticated' })
    renderApp('/?date=2026-11-10')
    act(() => useAuth.getState().logout('expired'))
    await screen.findByRole('button', { name: 'Entrar' })

    vi.mocked(api.auth.login).mockResolvedValueOnce(session(ADMIN))
    await signIn(ADMIN.username)
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/?date=2026-11-10'))
  })
})
