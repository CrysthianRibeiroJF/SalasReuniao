import { type ReactNode, type Ref, useId } from 'react'

import { cn } from '../../lib/cn'

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
  className,
  ref,
  tone = 'video',
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: ReactNode
  description?: ReactNode
  disabled?: boolean
  className?: string
  ref?: Ref<HTMLButtonElement>
  tone?: 'video' | 'ink' | 'danger'
}) {
  const id = useId()
  const on = { video: 'bg-video', ink: 'bg-ink', danger: 'bg-danger' }[tone]
  return (
    <div className={cn('flex items-center justify-between gap-3', className)}>
      <label htmlFor={id} className="min-w-0 cursor-pointer select-none">
        <span className="block text-[13px] font-medium text-ink">{label}</span>
        {description && <span className="block text-[11.5px] text-muted">{description}</span>}
      </label>
      <button
        ref={ref}
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative inline-flex h-[22px] w-[40px] shrink-0 items-center rounded-full transition-colors disabled:opacity-50',
          checked ? on : 'bg-line-strong',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'absolute left-[3px] size-4 rounded-full bg-white shadow-[0_1px_2px_rgb(24_33_43/0.3)] transition-transform',
            checked && 'translate-x-[18px]',
          )}
        />
        <span className="sr-only">{checked ? 'Sim' : 'Não'}</span>
      </button>
    </div>
  )
}
