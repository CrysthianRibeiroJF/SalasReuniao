import { cn } from '../../lib/cn'
import { initials, userColor } from '../../lib/people'

export function Avatar({
  userId,
  name,
  size = 'md',
  className,
  ring = false,
}: {
  userId: number
  name: string
  size?: 'sm' | 'md'
  className?: string
  ring?: boolean
}) {
  return (
    <span
      aria-hidden="true"
      style={{ backgroundColor: userColor(userId) }}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full font-semibold tracking-wide text-white select-none',
        size === 'sm' ? 'size-5 text-[9px]' : 'size-7 text-[11px]',
        ring && 'ring-2 ring-surface',
        className,
      )}
    >
      {initials(name)}
    </span>
  )
}
