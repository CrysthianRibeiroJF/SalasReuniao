import { cn } from '../../lib/cn'

/** Marca: uma folha de grade com uma reserva destacada. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn('shrink-0', className)} aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="var(--color-ink)" />
      <rect x="7" y="8" width="18" height="17" rx="2" fill="none" stroke="#fff" strokeWidth="2" />
      <path d="M7 13h18M13 8v17" stroke="#fff" strokeWidth="2" />
      <rect x="15.5" y="15.5" width="7" height="6" rx="1" fill="#7d8bf3" />
    </svg>
  )
}
