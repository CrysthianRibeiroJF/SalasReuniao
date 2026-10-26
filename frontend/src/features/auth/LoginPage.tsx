import { type FormEvent, useEffect, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { Eye, EyeOff, LogIn } from 'lucide-react'

import { errorMessage, retryAfterSeconds } from '../../api/errors'
import { Button } from '../../components/ui/Button'
import { Field, Input } from '../../components/ui/Form'
import { LOGOUT_MESSAGES, sameUsername, useAuth } from '../../store/auth'
import { BrandMark } from '../common/BrandMark'

/** Relógio que bate a cada segundo enquanto houver um prazo (contagem regressiva do bloqueio). */
function useCountdownClock(until: number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!until) return
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [until])
  return now
}

/** "0:45", "1:30", "15:00". */
export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Página para onde ir depois de entrar: a anterior só se quem entrou é quem estava (R40). */
export function loginDestination(
  from: string | undefined,
  previousUsername: string | null,
  username: string | undefined,
): string {
  if (!from) return '/'
  if (previousUsername && !sameUsername(previousUsername, username)) return '/'
  return from
}

export function LoginPage() {
  const status = useAuth((s) => s.status)
  const logoutReason = useAuth((s) => s.logoutReason)
  const previousUsername = useAuth((s) => s.previousUsername)
  const currentUsername = useAuth((s) => s.user?.username)
  const login = useAuth((s) => s.login)
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  /** Bloqueio por tentativas demais (429): até quando (Date.now) o botão fica desabilitado. */
  const [blockedUntil, setBlockedUntil] = useState<number | null>(null)
  const now = useCountdownClock(blockedUntil)
  const remaining = blockedUntil ? Math.ceil((blockedUntil - now) / 1000) : 0
  const blocked = remaining > 0

  // O bloqueio acabou: some o aviso e o botão volta.
  useEffect(() => {
    if (blockedUntil && !blocked) {
      setBlockedUntil(null)
      setError(null)
    }
  }, [blockedUntil, blocked])

  if (status === 'authenticated') {
    return <Navigate to={loginDestination(from, previousUsername, currentUsername)} replace />
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (blocked) return
    if (!username.trim() || !password) {
      setError('Informe usuário e senha.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await login(username, password)
      navigate(loginDestination(from, previousUsername, useAuth.getState().user?.username), { replace: true })
    } catch (err) {
      const retryAfter = retryAfterSeconds(err)
      setBlockedUntil(retryAfter ? Date.now() + retryAfter * 1000 : null)
      setError(
        retryAfter
          ? errorMessage(err, 'Muitas tentativas de entrada sem sucesso.')
          : errorMessage(err, 'Não foi possível entrar. Tente novamente.'),
      )
      setSubmitting(false)
    }
  }

  const notice = logoutReason && logoutReason !== 'manual' ? LOGOUT_MESSAGES[logoutReason] : null

  return (
    <main className="relative flex h-full items-center justify-center overflow-hidden bg-paper px-4">
      <SheetBackdrop />
      <div className="relative w-full max-w-[380px]">
        <div className="mb-6 flex items-center gap-3">
          <BrandMark className="size-9" />
          <div>
            <p className="font-condensed text-[19px] leading-tight font-semibold tracking-tight text-ink">
              Agenda de Salas
            </p>
            <p className="text-[12.5px] text-muted">Recepção · reservas em tempo real</p>
          </div>
        </div>

        <form
          onSubmit={onSubmit}
          noValidate
          className="rounded-xl border border-line-strong bg-surface p-6 shadow-[0_1px_2px_rgb(24_33_43/0.05),0_12px_32px_-12px_rgb(24_33_43/0.18)]"
        >
          <h1 className="text-[16px] font-semibold text-ink">Entrar</h1>
          {notice && !error && (
            <p
              role="status"
              className="mt-3 rounded-md border border-warning/30 bg-warning-tint px-3 py-2 text-[12.5px] text-warning"
            >
              {notice}
            </p>
          )}
          {error && (
            <div
              role="alert"
              className="mt-3 rounded-md border border-danger/25 bg-danger-tint px-3 py-2 text-[12.5px] text-danger-ink"
            >
              <p>{error}</p>
              {blocked && (
                <p className="mt-0.5 font-medium" data-testid="login-countdown">
                  Tente novamente em <span className="tabular font-mono">{formatCountdown(remaining)}</span>.
                </p>
              )}
            </div>
          )}

          <div className="mt-4 flex flex-col gap-3.5">
            <Field label="Usuário">
              {(props) => (
                <Input
                  {...props}
                  autoFocus
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              )}
            </Field>
            <Field label="Senha">
              {(props) => (
                <div className="relative">
                  <Input
                    {...props}
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? 'Ocultar senha' : 'Mostrar senha'}
                    className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-1 text-muted hover:text-ink"
                  >
                    {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  </button>
                </div>
              )}
            </Field>
          </div>

          <Button
            type="submit"
            variant="primary"
            loading={submitting}
            disabled={blocked}
            icon={<LogIn className="size-4" />}
            className="mt-5 w-full"
          >
            Entrar
          </Button>
        </form>
        <p className="mt-4 text-center text-[12px] text-muted">
          Esqueceu a senha? Fale com a administração da recepção.
        </p>
      </div>
    </main>
  )
}

/** Fundo com a malha da planilha, esmaecida nas bordas. */
function SheetBackdrop() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0"
      style={{
        backgroundImage:
          'linear-gradient(to right, var(--color-line) 1px, transparent 1px), linear-gradient(to bottom, var(--color-line) 1px, transparent 1px)',
        backgroundSize: '132px 28px',
        maskImage: 'radial-gradient(ellipse 70% 60% at 50% 45%, black 10%, transparent 75%)',
        WebkitMaskImage: 'radial-gradient(ellipse 70% 60% at 50% 45%, black 10%, transparent 75%)',
        opacity: 0.9,
      }}
    />
  )
}
