import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { Pencil, Plus, Power, UserRound } from 'lucide-react'

import { errorMessage, isApiError } from '../../api/errors'
import { api } from '../../api/endpoints'
import type { UserOut, UserRole, UserUpdate } from '../../api/types'
import { Avatar } from '../../components/ui/Avatar'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Dialog } from '../../components/ui/Dialog'
import { EmptyState } from '../../components/ui/EmptyState'
import { Field, Input } from '../../components/ui/Form'
import { Segmented } from '../../components/ui/Segmented'
import { Skeleton } from '../../components/ui/Skeleton'
import { Switch } from '../../components/ui/Switch'
import { Tooltip } from '../../components/ui/Tooltip'
import { cn } from '../../lib/cn'
import { ROLE_LABELS } from '../../lib/labels'
import { formatDateTime } from '../../lib/time'
import { useAuth } from '../../store/auth'
import { useLoadedConfig } from '../../store/config'
import { toast } from '../../store/toasts'
import { DataTable, PageHeader, Td, Th } from './AdminLayout'

const collator = new Intl.Collator('pt-BR', { sensitivity: 'base' })

export function UsersAdminPage() {
  const config = useLoadedConfig()
  const me = useAuth((s) => s.user)
  const [users, setUsers] = useState<UserOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<{ user: UserOut | null } | null>(null)
  const [toggling, setToggling] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setError(null)
      const list = await api.users.list()
      setUsers(
        list.sort((a, b) => Number(b.is_active) - Number(a.is_active) || collator.compare(a.full_name, b.full_name)),
      )
    } catch (err) {
      setError(errorMessage(err, 'Não foi possível carregar os usuários.'))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const replace = (user: UserOut) =>
    setUsers((list) => (list ? list.map((u) => (u.id === user.id ? user : u)) : [user]))

  const toggleActive = async (user: UserOut) => {
    setToggling(user.id)
    try {
      const updated = await api.users.update(user.id, { is_active: !user.is_active })
      replace(updated)
      toast.success(
        updated.is_active ? `${updated.full_name} foi reativado(a)` : `${updated.full_name} foi desativado(a)`,
      )
    } catch (err) {
      toast.error(errorMessage(err, 'Não foi possível alterar o usuário.'))
    } finally {
      setToggling(null)
    }
  }

  return (
    <>
      <PageHeader
        title="Usuários"
        description="Recepcionistas e administradores com acesso à agenda. Usuários desativados não conseguem entrar."
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setDialog({ user: null })}>
            Novo usuário
          </Button>
        }
      />

      {error ? (
        <EmptyState
          title="Erro ao carregar"
          description={error}
          action={<Button onClick={() => void load()}>Tentar de novo</Button>}
        />
      ) : users === null ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-11" />
          ))}
        </div>
      ) : users.length === 0 ? (
        <EmptyState icon={<UserRound className="size-5" />} title="Nenhum usuário" />
      ) : (
        <DataTable>
          <thead>
            <tr>
              <Th>Nome</Th>
              <Th>Usuário</Th>
              <Th>Papel</Th>
              <Th>Situação</Th>
              <Th>Último acesso</Th>
              <Th className="w-52" />
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const isMe = user.id === me?.id
              return (
                <tr key={user.id} className={cn('hover:bg-paper/60', !user.is_active && 'text-muted')}>
                  <Td>
                    <span className="flex items-center gap-2.5">
                      <Avatar
                        userId={user.id}
                        name={user.full_name}
                        size="sm"
                        className={cn(!user.is_active && 'opacity-50')}
                      />
                      <span className="font-medium">{user.full_name}</span>
                      {isMe && <span className="text-[11.5px] text-muted">(você)</span>}
                    </span>
                  </Td>
                  <Td className="font-mono text-[12.5px]">{user.username}</Td>
                  <Td>
                    <Badge tone={user.role === 'ADMIN' ? 'ink' : 'neutral'}>{ROLE_LABELS[user.role]}</Badge>
                  </Td>
                  <Td>
                    {user.is_active ? <Badge tone="success">Ativo</Badge> : <Badge tone="neutral">Desativado</Badge>}
                  </Td>
                  <Td className="tabular font-mono text-[12px] text-ink-2">
                    {user.last_login_at ? (
                      formatDateTime(user.last_login_at, config.timezone)
                    ) : (
                      <span className="text-faint">nunca</span>
                    )}
                  </Td>
                  <Td className="text-right whitespace-nowrap">
                    <Button
                      size="xs"
                      variant="ghost"
                      icon={<Pencil className="size-3.5" />}
                      onClick={() => setDialog({ user })}
                    >
                      Editar
                    </Button>
                    <Tooltip content={isMe ? 'Você não pode desativar a si mesmo(a).' : undefined}>
                      <Button
                        size="xs"
                        variant="ghost"
                        icon={<Power className="size-3.5" />}
                        disabled={isMe}
                        loading={toggling === user.id}
                        onClick={() => void toggleActive(user)}
                        className={
                          user.is_active ? 'text-danger hover:bg-danger-tint' : 'text-success hover:bg-success-tint'
                        }
                      >
                        {user.is_active ? 'Desativar' : 'Reativar'}
                      </Button>
                    </Tooltip>
                  </Td>
                </tr>
              )
            })}
          </tbody>
        </DataTable>
      )}

      <Dialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        title={dialog?.user ? `Editar ${dialog.user.full_name}` : 'Novo usuário'}
      >
        {dialog && (
          <UserForm
            user={dialog.user}
            isMe={dialog.user?.id === me?.id}
            onSaved={(user) => {
              if (dialog.user) replace(user)
              else setUsers((list) => [...(list ?? []), user])
              setDialog(null)
            }}
            onCancel={() => setDialog(null)}
          />
        )}
      </Dialog>
    </>
  )
}

type Errors = Partial<Record<'username' | 'fullName' | 'password', string>>

export function UserForm({
  user,
  isMe,
  onSaved,
  onCancel,
}: {
  user: UserOut | null
  isMe: boolean
  onSaved: (user: UserOut) => void
  onCancel: () => void
}) {
  const [username, setUsername] = useState(user?.username ?? '')
  const [fullName, setFullName] = useState(user?.full_name ?? '')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<UserRole>(user?.role ?? 'RECEPTIONIST')
  const [isActive, setIsActive] = useState(user?.is_active ?? true)
  const [errors, setErrors] = useState<Errors>({})
  const [banner, setBanner] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const usernameRef = useRef<HTMLInputElement>(null)

  const validate = (): Errors => {
    const found: Errors = {}
    if (!user) {
      if (username.trim().length < 2) found.username = 'Use ao menos 2 caracteres.'
      else if (!/^[A-Za-z0-9._-]+$/.test(username.trim()))
        found.username = 'Use só letras sem acento, números, ponto, hífen ou sublinhado.'
    }
    if (fullName.trim().length < 2) found.fullName = 'Informe o nome completo.'
    if (!user && password.length < 6) found.password = 'A senha precisa de ao menos 6 caracteres.'
    if (user && password && password.length < 6) found.password = 'A senha precisa de ao menos 6 caracteres.'
    return found
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const found = validate()
    setErrors(found)
    if (Object.keys(found).length > 0) return
    setSaving(true)
    setBanner(null)
    try {
      let saved: UserOut
      if (user) {
        const patch: UserUpdate = {}
        if (fullName.trim() !== user.full_name) patch.full_name = fullName.trim()
        if (password) patch.password = password
        if (role !== user.role) patch.role = role
        if (isActive !== user.is_active) patch.is_active = isActive
        saved = Object.keys(patch).length > 0 ? await api.users.update(user.id, patch) : user
      } else {
        saved = await api.users.create({ username: username.trim(), full_name: fullName.trim(), password, role })
      }
      toast.success(user ? 'Usuário atualizado' : 'Usuário criado', { description: saved.full_name })
      onSaved(saved)
    } catch (err) {
      if (isApiError(err) && err.code === 'USERNAME_TAKEN') {
        setErrors({ username: err.message || 'Este nome de usuário já existe.' })
        usernameRef.current?.focus()
      } else {
        setBanner(errorMessage(err, 'Não foi possível salvar o usuário.'))
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      {banner && (
        <p
          role="alert"
          className="rounded-md border border-danger/25 bg-danger-tint px-3 py-2 text-[12.5px] text-danger-ink"
        >
          {banner}
        </p>
      )}
      {!user && (
        <Field label="Usuário" required hint="Usado para entrar (ex.: maria.silva)" error={errors.username}>
          {(props) => (
            <Input
              {...props}
              ref={usernameRef}
              value={username}
              maxLength={50}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              onChange={(e) => setUsername(e.target.value)}
              className="font-mono"
            />
          )}
        </Field>
      )}
      <Field label="Nome completo" required error={errors.fullName}>
        {(props) => <Input {...props} value={fullName} maxLength={150} onChange={(e) => setFullName(e.target.value)} />}
      </Field>
      <Field
        label={user ? 'Nova senha' : 'Senha'}
        required={!user}
        hint={user ? 'Deixe em branco para manter a senha atual.' : 'Mínimo de 6 caracteres.'}
        error={errors.password}
      >
        {(props) => (
          <Input
            {...props}
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        )}
      </Field>
      <div className="flex flex-col gap-1">
        <span className="text-[12px] font-medium text-ink-2">Papel</span>
        <Segmented<UserRole>
          label="Papel"
          value={role}
          onChange={setRole}
          className="self-start"
          options={[
            { value: 'RECEPTIONIST', label: ROLE_LABELS.RECEPTIONIST },
            { value: 'ADMIN', label: ROLE_LABELS.ADMIN },
          ]}
        />
        {isMe && role !== 'ADMIN' && (
          <p className="text-[11.5px] text-warning">Você não pode remover o próprio papel de administração.</p>
        )}
      </div>
      {user && (
        <Switch
          checked={isActive}
          onChange={setIsActive}
          disabled={isMe}
          tone="ink"
          label="Usuário ativo"
          description={
            isMe ? 'Você não pode desativar a si mesmo(a).' : isActive ? 'Pode entrar na agenda' : 'Não consegue entrar'
          }
        />
      )}
      <div className="flex justify-end gap-2 border-t border-line pt-3">
        <Button onClick={onCancel} disabled={saving}>
          Cancelar
        </Button>
        <Button type="submit" variant="primary" loading={saving}>
          {user ? 'Salvar alterações' : 'Criar usuário'}
        </Button>
      </div>
    </form>
  )
}
