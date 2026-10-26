import { cn } from '../../../lib/cn'
import { seatsLabel } from '../../../lib/rooms'

/** "· 6 lugares" no fim de um botão de sala (em destaque quando a sala é pequena para a reunião). */
export function SeatsSuffix({ capacity, tooSmall }: { capacity: number; tooSmall: boolean }) {
  const label = seatsLabel(capacity)
  if (!label) return null
  return (
    <span className={cn('font-normal', tooSmall ? 'text-danger-ink' : 'opacity-70')} aria-hidden="true">
      · {label}
    </span>
  )
}

/** Nome acessível de um botão de sala: "Mover para Sala 09 · 40 lugares". */
export function roomOptionLabel(action: string, capacity: number): string {
  const seats = seatsLabel(capacity)
  return seats ? `${action} · ${seats}` : action
}
