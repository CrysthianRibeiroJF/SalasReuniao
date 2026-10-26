import { type KeyboardEvent, type ReactNode, useRef } from 'react'

import { cn } from '../../lib/cn'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  /** Texto acessível/dica quando o rótulo é curto ou só ícone. */
  title?: string
  hint?: ReactNode
  disabled?: boolean
}

/** Controle segmentado (radiogroup): setas movem e escolhem, como um grupo de rádio nativo. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  size = 'md',
  className,
  itemClassName,
}: {
  options: readonly SegmentedOption<T>[]
  value: T
  onChange: (value: T) => void
  label: string
  size?: 'sm' | 'md'
  className?: string
  itemClassName?: string
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const enabled = options.filter((o) => !o.disabled)

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const delta = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key]
    if (!delta) return
    event.preventDefault()
    event.stopPropagation()
    const index = enabled.findIndex((o) => o.value === value)
    const next = enabled[(index + delta + enabled.length) % enabled.length]
    if (!next) return
    onChange(next.value)
    refs.current[options.indexOf(next)]?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn('inline-flex items-stretch rounded-md border border-line-strong bg-paper p-0.5', className)}
    >
      {options.map((option, i) => {
        const selected = option.value === value
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[i] = el
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            title={option.title}
            disabled={option.disabled}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(option.value)}
            onKeyDown={onKeyDown}
            className={cn(
              'inline-flex items-center justify-center gap-1.5 rounded-[5px] font-medium whitespace-nowrap transition-colors disabled:opacity-40',
              size === 'sm' ? 'h-6 px-2 text-[12px]' : 'h-7 px-2.5 text-[12.5px]',
              selected
                ? 'bg-surface text-ink shadow-[0_1px_2px_rgb(24_33_43/0.12),0_0_0_1px_rgb(24_33_43/0.06)]'
                : 'text-muted hover:text-ink',
              itemClassName,
            )}
          >
            {option.label}
            {option.hint}
          </button>
        )
      })}
    </div>
  )
}
