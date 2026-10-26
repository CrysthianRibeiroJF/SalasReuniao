import { useNavigate } from 'react-router'
import { CalendarDays, ChevronDown, History, LogOut, Settings } from 'lucide-react'

import { Avatar } from '../../components/ui/Avatar'
import { Menu, type MenuItem } from '../../components/ui/Menu'
import { ROLE_LABELS } from '../../lib/labels'
import { firstName } from '../../lib/people'
import { useAuth } from '../../store/auth'

export function UserMenu() {
  const user = useAuth((s) => s.user)
  const logout = useAuth((s) => s.logout)
  const navigate = useNavigate()
  if (!user) return null

  const isAdmin = user.role === 'ADMIN'
  const items: MenuItem[] = [
    { key: 'grid', label: 'Grade de salas', icon: <CalendarDays className="size-4" />, onSelect: () => navigate('/') },
    isAdmin
      ? {
          key: 'admin',
          label: 'Administração',
          icon: <Settings className="size-4" />,
          onSelect: () => navigate('/admin/salas'),
        }
      : {
          key: 'audit',
          label: 'Auditoria',
          icon: <History className="size-4" />,
          onSelect: () => navigate('/admin/auditoria'),
        },
    {
      key: 'logout',
      label: 'Sair',
      icon: <LogOut className="size-4" />,
      tone: 'danger',
      onSelect: () => logout('manual'),
    },
  ]

  return (
    <Menu
      triggerLabel={`Menu de ${user.full_name}`}
      trigger={
        <>
          <Avatar userId={user.id} name={user.full_name} />
          <span className="hidden max-w-[110px] truncate text-[13px] font-medium text-ink xl:inline">
            {firstName(user.full_name)}
          </span>
          <ChevronDown className="size-3.5 text-muted" aria-hidden="true" />
        </>
      }
      header={
        <>
          <p className="truncate text-[13px] font-semibold text-ink">{user.full_name}</p>
          <p className="text-[12px] text-muted">
            {user.username} · {ROLE_LABELS[user.role]}
          </p>
        </>
      }
      items={items}
    />
  )
}
