import type { ReactNode } from 'react'
import { Link, Navigate, NavLink, Outlet } from 'react-router'
import { ArrowLeft, DoorOpen, FileSpreadsheet, History, Users } from 'lucide-react'

import { cn } from '../../lib/cn'
import { useIsAdmin } from '../../store/auth'
import { BrandMark } from '../common/BrandMark'
import { ConnectionIndicator } from '../common/ConnectionIndicator'
import { UserMenu } from '../common/UserMenu'

const LINKS = [
  { to: 'salas', label: 'Salas', icon: DoorOpen, adminOnly: true },
  { to: 'usuarios', label: 'Usuários', icon: Users, adminOnly: true },
  { to: 'importar', label: 'Importar planilha', icon: FileSpreadsheet, adminOnly: true },
  { to: 'auditoria', label: 'Auditoria', icon: History, adminOnly: false },
] as const

export function AdminLayout() {
  const isAdmin = useIsAdmin()
  const links = LINKS.filter((l) => isAdmin || !l.adminOnly)

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line-strong bg-surface px-3">
        <Link
          to="/"
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[13px] font-medium text-ink-2 hover:bg-ink/[0.05] hover:text-ink"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
          Grade
        </Link>
        <span className="h-5 w-px bg-line-strong" aria-hidden="true" />
        <BrandMark className="size-6" />
        <p className="font-condensed text-[15px] font-semibold tracking-tight text-ink">
          {isAdmin ? 'Administração' : 'Auditoria'}
        </p>
        <div className="ml-auto flex items-center gap-3">
          <ConnectionIndicator />
          <UserMenu />
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        {links.length > 1 && (
          <nav aria-label="Seções da administração" className="w-56 shrink-0 border-r border-line bg-surface p-2.5">
            <ul className="space-y-0.5">
              {links.map(({ to, label, icon: Icon }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium',
                        isActive ? 'bg-ink text-white' : 'text-ink-2 hover:bg-paper hover:text-ink',
                      )
                    }
                  >
                    <Icon className="size-4" aria-hidden="true" />
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        )}
        <main className="scroll-thin min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl px-6 py-5">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}

export function AdminIndex() {
  const isAdmin = useIsAdmin()
  return <Navigate to={isAdmin ? 'salas' : 'auditoria'} replace />
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="font-condensed text-[22px] leading-tight font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-0.5 max-w-2xl text-[13px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

/** Tabela no estilo da grade (cabeçalho fixo, linhas densas). */
export function DataTable({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('overflow-x-auto rounded-lg border border-line-strong bg-surface', className)}>
      <table className="w-full border-collapse text-left text-[13px]">{children}</table>
    </div>
  )
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      scope="col"
      className={cn(
        'border-b border-line-strong bg-paper px-3 py-2 text-[11px] font-semibold tracking-wide whitespace-nowrap text-muted uppercase',
        className,
      )}
    >
      {children}
    </th>
  )
}

export function Td({ children, className }: { children?: ReactNode; className?: string }) {
  return <td className={cn('border-b border-line px-3 py-2 align-middle', className)}>{children}</td>
}
