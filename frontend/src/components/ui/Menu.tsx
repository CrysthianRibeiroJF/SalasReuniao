import { type KeyboardEvent, type ReactNode, useRef, useState } from 'react'

import { useDismiss } from '../../hooks/useDismiss'
import { cn } from '../../lib/cn'

export interface MenuItem {
  key: string
  label: ReactNode
  icon?: ReactNode
  onSelect: () => void
  tone?: 'default' | 'danger'
  disabled?: boolean
}

/** Menu suspenso simples (botão + lista), com setas, Enter e Esc. */
export function Menu({
  trigger,
  triggerLabel,
  items,
  header,
  align = 'end',
  className,
}: {
  trigger: ReactNode
  triggerLabel: string
  items: MenuItem[]
  header?: ReactNode
  align?: 'start' | 'end'
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  useDismiss(rootRef, () => setOpen(false), { enabled: open })

  const focusItem = (index: number) => {
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')
    if (!buttons || buttons.length === 0) return
    buttons[(index + buttons.length) % buttons.length]?.focus()
  }

  const onListKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const buttons = [
      ...(listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []),
    ]
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      focusItem(index + 1)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      focusItem(index - 1)
    } else if (event.key === 'Tab') {
      setOpen(false)
    }
  }

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={triggerLabel}
        onClick={() => {
          setOpen((v) => !v)
          requestAnimationFrame(() => focusItem(0))
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault()
            setOpen(true)
            requestAnimationFrame(() => focusItem(0))
          }
        }}
        className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-ink/[0.05]"
      >
        {trigger}
      </button>
      {open && (
        <div
          ref={listRef}
          role="menu"
          onKeyDown={onListKeyDown}
          className={cn(
            'absolute top-full z-50 mt-1 min-w-[220px] animate-pop-in rounded-lg border border-line-strong bg-surface py-1 shadow-float',
            align === 'end' ? 'right-0' : 'left-0',
          )}
        >
          {header && <div className="border-b border-line px-3 pt-1.5 pb-2">{header}</div>}
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={() => {
                setOpen(false)
                buttonRef.current?.focus()
                item.onSelect()
              }}
              className={cn(
                'flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-[13px] outline-none hover:bg-paper focus-visible:bg-paper disabled:opacity-50',
                item.tone === 'danger' ? 'text-danger' : 'text-ink',
              )}
            >
              {item.icon && <span className="text-muted">{item.icon}</span>}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
