import { useMemo } from 'react'

import type { OnlineUser } from '../../api/types'
import { Avatar } from '../../components/ui/Avatar'
import { Tooltip } from '../../components/ui/Tooltip'
import { useAuth } from '../../store/auth'
import { usePresence } from '../../store/presence'

const MAX_VISIBLE = 4

const collator = new Intl.Collator('pt-BR', { sensitivity: 'base' })

function describe(user: OnlineUser): string {
  return user.connections > 1 ? `${user.full_name} · ${user.connections} abas abertas` : user.full_name
}

/** Colegas com a agenda aberta agora (você já aparece no menu do usuário). */
export function PresenceAvatars() {
  const online = usePresence((s) => s.online)
  const meId = useAuth((s) => s.user?.id)

  const ordered = useMemo(
    () => online.filter((u) => u.id !== meId).sort((a, b) => collator.compare(a.full_name, b.full_name)),
    [online, meId],
  )

  if (ordered.length === 0) return null
  const visible = ordered.slice(0, MAX_VISIBLE)
  const hidden = ordered.slice(MAX_VISIBLE)

  return (
    <div
      className="flex items-center"
      role="group"
      aria-label={`${ordered.length} ${ordered.length === 1 ? 'colega online' : 'colegas online'}`}
    >
      <div className="flex -space-x-1.5">
        {visible.map((user) => (
          <Tooltip key={user.id} content={describe(user)}>
            <span tabIndex={0} className="rounded-full" aria-label={describe(user)}>
              <Avatar userId={user.id} name={user.full_name} ring />
            </span>
          </Tooltip>
        ))}
        {hidden.length > 0 && (
          <Tooltip
            content={
              <ul className="space-y-0.5">
                {hidden.map((u) => (
                  <li key={u.id}>{describe(u)}</li>
                ))}
              </ul>
            }
          >
            <span
              tabIndex={0}
              aria-label={`Mais ${hidden.length} online`}
              className="inline-flex size-7 items-center justify-center rounded-full bg-sheet font-mono text-[10.5px] font-medium text-ink-2 ring-2 ring-surface"
            >
              +{hidden.length}
            </span>
          </Tooltip>
        )}
      </div>
    </div>
  )
}
