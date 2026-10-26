import type { InputHTMLAttributes, ReactNode, Ref, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'
import { useId } from 'react'

import { cn } from '../../lib/cn'

const CONTROL =
  'w-full rounded-md border bg-surface text-[13.5px] text-ink placeholder:text-faint transition-[border-color,box-shadow] outline-none ' +
  'focus:border-video focus:ring-3 focus:ring-video/15 disabled:bg-paper disabled:text-muted'

function controlClass(invalid: boolean | undefined, className?: string): string {
  return cn(
    CONTROL,
    invalid ? 'border-danger focus:border-danger focus:ring-danger/15' : 'border-line-strong',
    className,
  )
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean
  ref?: Ref<HTMLInputElement>
}

export function Input({ invalid, className, ...rest }: InputProps) {
  return (
    <input
      aria-invalid={invalid || undefined}
      className={controlClass(invalid, cn('h-9 px-2.5', className))}
      {...rest}
    />
  )
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean
  ref?: Ref<HTMLSelectElement>
}

export function Select({ invalid, className, children, ...rest }: SelectProps) {
  return (
    <select
      aria-invalid={invalid || undefined}
      className={controlClass(
        invalid,
        cn('h-9 appearance-none bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-8 pl-2.5', className),
      )}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%235b6774' stroke-width='1.6'%3E%3Cpath d='m4.5 6.5 3.5 3.5 3.5-3.5'/%3E%3C/svg%3E\")",
      }}
      {...rest}
    >
      {children}
    </select>
  )
}

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean
  ref?: Ref<HTMLTextAreaElement>
}

export function Textarea({ invalid, className, ...rest }: TextareaProps) {
  return (
    <textarea
      aria-invalid={invalid || undefined}
      className={controlClass(invalid, cn('px-2.5 py-2 leading-snug', className))}
      {...rest}
    />
  )
}

/** Rótulo + controle + dica/erro. `children` recebe o id a ser usado no controle. */
export function Field({
  label,
  hint,
  error,
  required,
  className,
  trailing,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: string | null
  required?: boolean
  className?: string
  trailing?: ReactNode
  children: (props: { id: string; 'aria-describedby'?: string; invalid: boolean }) => ReactNode
}) {
  const id = useId()
  const messageId = `${id}-message`
  const message = error || hint
  return (
    <div className={cn('flex min-w-0 flex-col gap-1', className)}>
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-[12px] font-medium text-ink-2">
          {label}
          {required && (
            <span className="ml-0.5 text-danger" aria-hidden="true">
              *
            </span>
          )}
        </label>
        {trailing}
      </div>
      {children({ id, 'aria-describedby': message ? messageId : undefined, invalid: Boolean(error) })}
      {message && (
        <p id={messageId} className={cn('text-[11.5px] leading-snug', error ? 'text-danger' : 'text-muted')}>
          {message}
        </p>
      )}
    </div>
  )
}
